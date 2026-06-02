"""
gossip.py — UDP gossip for automatic peer discovery.

How it works:
  - On startup, every router instance starts broadcasting a small JSON
    "hello" packet to the local network via UDP broadcast every few seconds.
  - Every router instance also listens for these packets.
  - When a packet arrives from an unknown peer, that peer is added to the
    registry automatically — no config change needed.
  - If a peer stops broadcasting for longer than `peer_timeout` seconds,
    it is considered dead and removed from the live peer list.

Why UDP broadcast?
  Simple and zero-config. Works on a LAN or on the same machine (loopback).
  It is not reliable (packets can be lost) but that is fine — we broadcast
  frequently enough that occasional loss doesn't matter.

Limitations (fine for our current phase):
  - Works on a single subnet only. Cross-subnet needs multicast or a registry.
  - No authentication — any process on the network can join the mesh.
    (mTLS authentication comes in Phase 5.)
"""

import asyncio
import json
import logging
import socket
import time
from dataclasses import dataclass, field

log = logging.getLogger("router.gossip")

# The broadcast address that reaches all hosts on the local subnet.
BROADCAST_ADDR = "255.255.255.255"


@dataclass
class GossipPeerState:
    """
    Tracks what we know about a peer we discovered via gossip.
    This is separate from the Peer object in peers.py — gossip only handles
    discovery. The health poller in peers.py handles the load metrics.
    """
    node_id: str
    proxy_url: str             # the peer's HTTP proxy address e.g. http://192.168.1.5:8000
    last_seen: float = field(default_factory=time.time)  # epoch seconds


class GossipProtocol(asyncio.DatagramProtocol):
    """
    asyncio UDP protocol that receives broadcast packets and hands them
    to the GossipManager for processing.
    """

    def __init__(self, on_packet):
        # on_packet is a callback — called with (data: bytes, addr: tuple) for each packet.
        self._on_packet = on_packet

    def datagram_received(self, data: bytes, addr: tuple):
        self._on_packet(data, addr)

    def error_received(self, exc: Exception):
        log.debug(f"UDP error: {exc}")


class GossipManager:
    """
    Manages gossip-based peer discovery.

    Usage (called by router/main.py):
        gossip = GossipManager(config, my_node_id, my_proxy_url)
        await gossip.start()
        ...
        peers = gossip.live_peers()   # returns list of GossipPeerState
        await gossip.stop()
    """

    def __init__(self, config: dict, node_id: str, proxy_url: str):
        self._port = config.get("port", 6000)
        self._announce_interval = config.get("announce_interval", 4)
        self._peer_timeout = config.get("peer_timeout", 15)
        self._node_id = node_id
        self._proxy_url = proxy_url

        # Known peers, keyed by node_id. Includes ourselves — we filter on read.
        self._peers: dict[str, GossipPeerState] = {}

        self._transport = None
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Open the UDP socket, start announcing and listening."""
        loop = asyncio.get_event_loop()

        # Create a UDP socket with broadcast enabled.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # SO_REUSEPORT lets multiple processes on the same machine share the port.
        # Needed when running two router instances locally for testing.
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(("", self._port))
        sock.setblocking(False)

        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: GossipProtocol(self._handle_packet),
            sock=sock,
        )

        # Start the announce loop and the eviction loop as background tasks.
        self._tasks.append(asyncio.create_task(self._announce_loop()))
        self._tasks.append(asyncio.create_task(self._evict_loop()))

        log.info(f"Gossip started — node_id={self._node_id}, port={self._port}")

    async def stop(self):
        """Cancel background tasks and close the UDP socket."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._transport:
            self._transport.close()

    # ── Announcing ────────────────────────────────────────────────────────────

    async def _announce_loop(self):
        """Broadcast this node's info to the subnet on a fixed interval."""
        while True:
            self._broadcast()
            await asyncio.sleep(self._announce_interval)

    def _broadcast(self):
        """Send one UDP broadcast packet containing this node's identity."""
        packet = json.dumps({
            "node_id": self._node_id,
            "proxy_url": self._proxy_url,
        }).encode()

        try:
            self._transport.sendto(packet, (BROADCAST_ADDR, self._port))
            log.debug(f"Announced self → {BROADCAST_ADDR}:{self._port}")
        except Exception as exc:
            log.debug(f"Broadcast failed: {exc}")

    # ── Receiving ─────────────────────────────────────────────────────────────

    def _handle_packet(self, data: bytes, addr: tuple):
        """
        Called for every incoming UDP packet.
        Parse it and update the known peer table.
        """
        try:
            msg = json.loads(data.decode())
            node_id = msg["node_id"]
            proxy_url = msg["proxy_url"]
        except (json.JSONDecodeError, KeyError):
            return  # malformed packet — ignore

        is_new = node_id not in self._peers
        self._peers[node_id] = GossipPeerState(
            node_id=node_id,
            proxy_url=proxy_url,
            last_seen=time.time(),
        )

        if is_new and node_id != self._node_id:
            log.info(f"Discovered new peer via gossip: {node_id} @ {proxy_url}")

    # ── Eviction ──────────────────────────────────────────────────────────────

    async def _evict_loop(self):
        """Periodically remove peers we haven't heard from recently."""
        while True:
            await asyncio.sleep(self._announce_interval)
            self._evict_stale()

    def _evict_stale(self):
        """Remove any peer whose last_seen is older than peer_timeout."""
        now = time.time()
        stale = [
            nid for nid, state in self._peers.items()
            if now - state.last_seen > self._peer_timeout and nid != self._node_id
        ]
        for nid in stale:
            log.warning(f"Evicting stale gossip peer: {nid} (no heartbeat for {self._peer_timeout}s)")
            del self._peers[nid]

    # ── Public interface ───────────────────────────────────────────────────────

    def live_peers(self) -> list[GossipPeerState]:
        """
        Return all peers we've heard from recently, excluding ourselves.
        peers.py calls this to sync its Peer list with gossip discoveries.
        """
        return [
            state for nid, state in self._peers.items()
            if nid != self._node_id
        ]