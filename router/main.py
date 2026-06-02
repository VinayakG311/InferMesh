"""
main.py — The router service.

Clients talk to this instead of talking to a node directly.
For every request the router:
  1. Asks the peer registry for alive peers.
  2. Asks the scorer which peer is best right now.
  3. Forwards the request to that peer via forwarder.py.
  4. If that peer fails, retries on the next best peer.
  5. If all peers fail, returns 503.

New in Phase 4:
  - Gossip discovery: peers join/leave the mesh automatically.
  - OpenRouter spillover: remote fallback peer when all local nodes are saturated.

Run with:
    NODE_ID=node-a PROXY_URL=http://localhost:8000 uvicorn router.main:app --port 9000
"""

import logging
import os
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from router.peers import PeerRegistry
from router import scorer
from router import forwarder

logging.basicConfig(
    level=logging.DEBUG if os.getenv("DEBUG") else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("router.main")

CONFIG_PATH = os.getenv("ROUTER_CONFIG", "router/config.yaml")

# This router's own identity — broadcast via gossip so other routers find it.
# NODE_ID: unique name for this instance (e.g. "node-a").
# PROXY_URL: the proxy this router sits in front of (e.g. "http://localhost:8000").
NODE_ID = os.getenv("NODE_ID", f"node-{uuid.uuid4().hex[:6]}")
PROXY_URL = os.getenv("PROXY_URL", "http://localhost:8000")

# ── Startup / shutdown ────────────────────────────────────────────────────────

registry = PeerRegistry(CONFIG_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pass our identity so the gossip layer can announce us to other routers.
    await registry.start(node_id=NODE_ID, proxy_url=PROXY_URL)
    yield
    await registry.stop()


app = FastAPI(title="Inference Router", version="0.2.0", lifespan=lifespan)

# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Router process is alive."""
    return {"status": "ok"}


@app.get("/health/ready")
async def ready():
    """Router is ready when at least one peer is alive."""
    alive = registry.alive_peers()
    if not alive:
        return JSONResponse(status_code=503, content={"status": "no_peers_alive"})
    return {"status": "ready", "alive_peers": [p.id for p in alive]}


# ── Peer visibility ───────────────────────────────────────────────────────────

@app.get("/peers")
async def peers():
    """
    Current state of every known peer.
    Shows score, latency, queue depth, and whether it was discovered via gossip.
    Useful for debugging routing decisions.
    """
    gossip_ids = set()
    if registry._gossip:
        gossip_ids = {s.node_id for s in registry._gossip.live_peers()}

    return [
        {
            "id": p.id,
            "url": p.url,
            "is_alive": p.is_alive,
            "latency_ms": round(p.latency_ms, 1),
            "queue_depth": p.queue_depth,
            "active_requests": p.active_requests,
            "score": round(scorer.score(p), 1) if p.is_alive else None,
            "discovered_via": "gossip" if p.id in gossip_ids else "static/config",
        }
        for p in registry.all_peers()
    ]


# ── Routing ───────────────────────────────────────────────────────────────────

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Main routing endpoint. Clients send requests here exactly as they would
    to a single node — peer selection is invisible to them.

    Retry logic: if the chosen peer fails, remove it from candidates and
    try the next best peer, until one succeeds or all have been tried.
    """
    payload = await request.json()
    is_stream = payload.get("stream", False)

    candidates = registry.alive_peers()
    if not candidates:
        raise HTTPException(status_code=503, detail="No peers available.")

    while candidates:
        peer = scorer.pick(candidates)
        candidates.remove(peer)

        # If this is an OpenRouter peer, inject the configured model name
        # into the payload — OpenRouter requires an explicit model field.
        if peer.id == "openrouter" and peer.model:
            payload = {**payload, "model": peer.model}

        log.info(f"Routing → {peer.id}  score={scorer.score(peer):.1f}")

        try:
            if is_stream:
                return StreamingResponse(
                    forwarder.forward_stream(peer.url, payload, peer.api_key),
                    media_type="text/event-stream",
                    headers={"X-Routed-To": peer.id},
                )
            else:
                result = await forwarder.forward_blocking(peer.url, payload, peer.api_key)
                return JSONResponse(content=result, headers={"X-Routed-To": peer.id})

        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.warning(f"{peer.id} failed: {exc}. Trying next peer...")
            continue

    raise HTTPException(status_code=503, detail="All peers failed to handle the request.")