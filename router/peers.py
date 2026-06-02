"""
peers.py — Peer registry for the router.

Two discovery modes, controlled by config.yaml:
  - Gossip (gossip.enabled: true)  — peers announce themselves via UDP broadcast.
    New nodes join automatically. Dead nodes are evicted after peer_timeout seconds.
  - Static (gossip.enabled: false) — peer list is read from config.yaml at startup.
    Same as the original Phase 3 behaviour.

In both modes, the health poller runs on the same schedule — it hits /health/ready
and /node/info on every known peer and updates their live state (latency, queue depth).
The scorer reads this state to make routing decisions.
"""

import asyncio
import logging
import time

import httpx
import yaml

log = logging.getLogger("router.peers")


class Peer:
    """One node in the cluster — holds identity and live state."""

    def __init__(self, id: str, url: str, api_key: str | None = None, base_latency_ms: float = 0):
        self.id = id
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.base_latency_ms = base_latency_ms

        # Live state — written by the health poller, read by the scorer.
        self.is_alive: bool = False
        self.queue_depth: int = 0
        self.active_requests: int = 0
        self.latency_ms: float = base_latency_ms
        self.model: str | None = None   # set by openrouter.py for remote peers


class PeerRegistry:
    """
    Keeps the peer list fresh — via gossip discovery or a static config list.

    Regardless of how peers are discovered, the health poller runs on every
    known peer so the scorer always has fresh load data.
    """

    def __init__(self, config_path: str):
        self._peers: dict[str, Peer] = {}   # keyed by peer id
        self._poll_interval: int = 5
        self._config: dict = {}
        self._gossip = None                  # set in start() if enabled
        self._openrouter_peer: Peer | None = None
        self._tasks: list[asyncio.Task] = []
        self._load(config_path)

    def _load(self, config_path: str):
        """Parse config.yaml. Always loads static peers as a fallback baseline."""
        with open(config_path) as f:
            self._config = yaml.safe_load(f)

        self._poll_interval = self._config["router"].get("health_check_interval", 5)

        gossip_enabled = self._config.get("gossip", {}).get("enabled", False)

        # Load static peers — used directly if gossip is off, or as a seed if gossip is on.
        for entry in self._config.get("peers", []):
            self._add_peer(Peer(
                id=entry["id"],
                url=entry["url"],
                api_key=entry.get("api_key"),
                base_latency_ms=entry.get("base_latency_ms", 0),
            ))

        if gossip_enabled:
            log.info("Gossip discovery enabled — peers will be discovered automatically.")
        else:
            log.info(f"Static peer list — {len(self._peers)} peers loaded.")

    def _add_peer(self, peer: Peer):
        if peer.id not in self._peers:
            log.info(f"Registered peer: {peer.id} → {peer.url}")
        self._peers[peer.id] = peer

    async def start(self, node_id: str, proxy_url: str):
        """
        Start the registry.

        node_id and proxy_url are this router's own identity — passed to the
        gossip manager so it can announce itself to other nodes.
        """
        # Set up OpenRouter peer if enabled.
        from router import openrouter
        or_config = self._config.get("openrouter", {})
        self._openrouter_peer = openrouter.build_peer(or_config)
        if self._openrouter_peer:
            self._add_peer(self._openrouter_peer)

        # Start gossip if enabled.
        gossip_config = self._config.get("gossip", {})
        if gossip_config.get("enabled", False):
            from router.gossip import GossipManager
            self._gossip = GossipManager(gossip_config, node_id, proxy_url)
            await self._gossip.start()

        # Run one immediate health poll so the router is ready on startup.
        await self._poll_all()

        # Background tasks: health polling + gossip sync (if gossip is on).
        self._tasks.append(asyncio.create_task(self._poll_loop()))
        if self._gossip:
            self._tasks.append(asyncio.create_task(self._gossip_sync_loop()))

        log.info(f"PeerRegistry started — polling every {self._poll_interval}s")

    async def stop(self):
        """Cancel all background tasks and shut down gossip."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._gossip:
            await self._gossip.stop()

    # ── Health polling ────────────────────────────────────────────────────────

    async def _poll_loop(self):
        while True:
            await asyncio.sleep(self._poll_interval)
            await self._poll_all()

    async def _poll_all(self):
        """Poll every known peer concurrently."""
        await asyncio.gather(*[self._poll_one(p) for p in self._peers.values()])

    async def _poll_one(self, peer: Peer):
        """
        For local peers: hit /health/ready and /node/info.
        For OpenRouter: delegate to openrouter.check_health().
        Never raises — failure just marks the peer dead.
        """
        # OpenRouter has a different health check (no /health/ready endpoint).
        if peer.id == "openrouter":
            from router import openrouter
            await openrouter.check_health(peer)
            return

        headers = {"Authorization": f"Bearer {peer.api_key}"} if peer.api_key else {}

        try:
            async with httpx.AsyncClient(timeout=3) as client:
                t0 = time.perf_counter()
                health = await client.get(f"{peer.url}/health/ready", headers=headers)
                latency_ms = (time.perf_counter() - t0) * 1000

                if health.status_code != 200:
                    _mark_dead(peer, f"status {health.status_code}")
                    return

                info = await client.get(f"{peer.url}/node/info", headers=headers)
                info_data = info.json() if info.status_code == 200 else {}

                peer.is_alive = True
                peer.latency_ms = latency_ms + peer.base_latency_ms
                peer.queue_depth = info_data.get("queue_depth", 0)
                peer.active_requests = info_data.get("active_requests", 0)

                log.debug(f"{peer.id} alive — {peer.latency_ms:.0f}ms  queue={peer.queue_depth}")

        except Exception as exc:
            _mark_dead(peer, str(exc))

    # ── Gossip sync ───────────────────────────────────────────────────────────

    async def _gossip_sync_loop(self):
        """
        Periodically sync the peer registry with what gossip has discovered.
        New peers are added; peers that gossip has evicted are removed.
        """
        while True:
            await asyncio.sleep(self._gossip._announce_interval)
            self._sync_from_gossip()

    def _sync_from_gossip(self):
        """Add newly discovered peers and remove gossip-evicted ones."""
        if not self._gossip:
            return

        # Add peers gossip knows about that we don't have yet.
        for state in self._gossip.live_peers():
            if state.node_id not in self._peers:
                self._add_peer(Peer(id=state.node_id, url=state.proxy_url))

        # Remove peers that gossip has evicted, but keep static + openrouter peers.
        gossip_ids = {s.node_id for s in self._gossip.live_peers()}
        static_ids = {e["id"] for e in self._config.get("peers", [])}
        protected_ids = static_ids | {"openrouter"}

        evicted = [
            pid for pid in list(self._peers)
            if pid not in gossip_ids and pid not in protected_ids
        ]
        for pid in evicted:
            log.warning(f"Removing gossip-evicted peer: {pid}")
            del self._peers[pid]

    # ── Public interface ──────────────────────────────────────────────────────

    def alive_peers(self) -> list[Peer]:
        return [p for p in self._peers.values() if p.is_alive]

    def all_peers(self) -> list[Peer]:
        return list(self._peers.values())


def _mark_dead(peer: Peer, reason: str):
    if peer.is_alive:
        log.warning(f"{peer.id} went dead — {reason}")
    peer.is_alive = False