"""
openrouter.py — OpenRouter spillover peer.

OpenRouter speaks the OpenAI API format, just like our local proxies do.
The only differences are:
  - It needs an Authorization header with your API key.
  - It doesn't have /health/ready or /node/info endpoints.
  - We can't measure real queue depth — we treat it as always 0 but give it
    a high base_latency_ms penalty in config so the scorer deprioritises it.

This module creates a Peer object for OpenRouter and provides a health check
function that works differently from local nodes (just pings the models list).
The rest of the system (scorer, forwarder, router/main) sees it as a plain Peer
and doesn't need to know it's remote.
"""

import logging
import os
import time

import httpx

from router.peers import Peer

log = logging.getLogger("router.openrouter")

OPENROUTER_BASE_URL = "https://openrouter.ai/api"


def build_peer(config: dict) -> Peer | None:
    """
    Read the [openrouter] block from config and return a Peer, or None if disabled.

    Called once at startup by router/main.py.
    """
    if not config.get("enabled", False):
        return None

    # API key can come from config file or environment variable.
    api_key = config.get("api_key") or os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        log.warning("OpenRouter enabled but no api_key set — skipping.")
        return None

    model = config.get("model", "meta-llama/llama-3.2-3b-instruct:free")
    base_latency_ms = config.get("base_latency_ms", 800)

    peer = Peer(
        id="openrouter",
        url=OPENROUTER_BASE_URL,
        api_key=api_key,
        base_latency_ms=base_latency_ms,
    )
    # Store the model name on the peer so forwarder.py can inject it into the payload.
    peer.model = model

    log.info(f"OpenRouter peer configured — model: {model}, penalty: {base_latency_ms}ms")
    return peer


async def check_health(peer: Peer) -> None:
    """
    Health check for OpenRouter — polls their /api/v1/models endpoint.

    Unlike local nodes, we can't get queue_depth or active_requests.
    We just verify the API key is valid and the service is reachable,
    then mark the peer alive with zeroed load metrics.

    Called by the health poller in peers.py on the same schedule as local nodes.
    """
    headers = {"Authorization": f"Bearer {peer.api_key}"}

    try:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OPENROUTER_BASE_URL}/v1/models", headers=headers)

        latency_ms = (time.perf_counter() - t0) * 1000

        if resp.status_code != 200:
            if peer.is_alive:
                log.warning(f"OpenRouter health check failed — status {resp.status_code}")
            peer.is_alive = False
            return

        # OpenRouter is reachable. We have no load data, so leave queue/active at 0.
        # The base_latency_ms penalty in the score is what keeps it deprioritised.
        peer.is_alive = True
        peer.latency_ms = latency_ms + peer.base_latency_ms
        peer.queue_depth = 0
        peer.active_requests = 0

        log.debug(f"openrouter alive — latency={peer.latency_ms:.0f}ms (includes {peer.base_latency_ms}ms penalty)")

    except Exception as exc:
        if peer.is_alive:
            log.warning(f"OpenRouter went dead — {exc}")
        peer.is_alive = False