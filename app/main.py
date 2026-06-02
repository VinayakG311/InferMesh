"""
FastAPI proxy that sits between clients and Ollama.

The proxy keeps this node observable and protects Ollama from too much load:
- requests are admitted through a small bounded queue
- overload returns HTTP 429
- health, readiness, node info, and Prometheus metrics are exposed
"""

import asyncio
import os
import time
import uuid
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest


# Basic node configuration. Environment variables make it easy to run
# multiple local nodes later without changing the code.
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "llama3.2:3b")
NODE_ID = os.getenv("NODE_ID", f"node-{uuid.uuid4().hex[:8]}")
MAX_CONCURRENT_REQUESTS = int(os.getenv("MAX_CONCURRENT_REQUESTS", "1"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "8"))


app = FastAPI(title="Inference Proxy", version="0.1.0")


# Counts every inference request by outcome.
# outcome labels: "success" | "error" | "rejected" (429)
# Use this to verify both nodes are receiving traffic in step 2b.
INFERENCE_REQUESTS = Counter(
    "proxy_inference_requests_total",
    "Total inference requests by outcome.",
    ["outcome"],
)

# Tracks requests sitting in the queue waiting for a slot.
# Spikes here mean the node is under load — key routing signal in Phase 3.
QUEUE_DEPTH = Gauge(
    "proxy_queue_depth",
    "Requests currently waiting in the admission queue.",
)

# Tracks requests actively streaming from Ollama right now.
# Combined with QUEUE_DEPTH gives a full picture of node load.
ACTIVE_REQUESTS = Gauge(
    "proxy_active_requests",
    "Requests currently being proxied to Ollama.",
)

# Measures time from request arrival to the first byte streamed back.
# The single most useful latency signal — compare across nodes in step 2b.
TTFT = Histogram(
    "proxy_time_to_first_token_seconds",
    "Time from request receipt to first token streamed to client.",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# Full end-to-end duration: from arrival to last byte of the stream.
# Longer than TTFT by however long generation takes.
TOTAL_LATENCY = Histogram(
    "proxy_request_duration_seconds",
    "Total request duration from arrival to stream close.",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)


class AdmissionQueue:
    """Small FIFO admission queue for Ollama requests."""

    def __init__(self, max_active: int, max_waiting: int):
        self.max_active = max_active
        self.max_waiting = max_waiting
        self.active = 0
        self.waiting = 0
        self.condition = asyncio.Condition()

    async def acquire(self) -> None:
        """Wait for capacity, or reject immediately if the queue is full."""
        async with self.condition:
            if self.active >= self.max_active and self.waiting >= self.max_waiting:
                raise HTTPException(status_code=429, detail="Proxy overloaded")

            if self.active >= self.max_active:
                self.waiting += 1
                QUEUE_DEPTH.set(self.waiting)
                try:
                    await self.condition.wait_for(lambda: self.active < self.max_active)
                finally:
                    self.waiting -= 1
                    QUEUE_DEPTH.set(self.waiting)

            self.active += 1
            ACTIVE_REQUESTS.set(self.active)

    async def release(self) -> None:
        """Release capacity and wake one waiting request."""
        async with self.condition:
            self.active -= 1
            ACTIVE_REQUESTS.set(self.active)
            self.condition.notify(1)

    def depth(self) -> int:
        return self.waiting


admission_queue = AdmissionQueue(MAX_CONCURRENT_REQUESTS, MAX_QUEUE_SIZE)



@app.get("/health")
async def health() -> dict:
    """Liveness check: the proxy process is running."""
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness() -> JSONResponse:
    """Readiness check: Ollama is reachable and the configured model exists."""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{OLLAMA_URL}/api/tags")
            response.raise_for_status()
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": f"ollama_unreachable: {exc}"},
        )

    models = response.json().get("models", [])
    model_names = [model.get("name", "") for model in models]
    model_ready = any(MODEL_NAME in name for name in model_names)

    if not model_ready:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "model_not_found"},
        )

    return JSONResponse(content={"status": "ready"})


@app.get("/node/info")
async def node_info() -> dict:
    """Routing metadata used later by the scoring function."""
    return {
        "node_id": NODE_ID,
        "model_name": MODEL_NAME,
        "queue_depth": admission_queue.depth(),
        "max_queue_size": MAX_QUEUE_SIZE,
        "active_requests": admission_queue.active,
        "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
    }


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type="text/plain; version=0.0.4")


@app.get("/api/tags")
async def list_models() -> Response:
    """Pass through Ollama model listing for simple client startup checks."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")

    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type", "application/json"),
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request) -> Response:
    """Forward OpenAI-compatible chat completions to Ollama."""
    payload = await request.json()
    arrived_at = time.perf_counter()

    # Reject immediately if queue is full — increments "rejected" counter.
    try:
        await admission_queue.acquire()
    except HTTPException:
        INFERENCE_REQUESTS.labels(outcome="rejected").inc()
        raise

    client = httpx.AsyncClient(timeout=None)
    upstream_stream = client.stream(
        "POST",
        f"{OLLAMA_URL}/v1/chat/completions",
        json=payload,
    )

    try:
        upstream_response = await upstream_stream.__aenter__()
    except Exception as exc:
        await client.aclose()
        await admission_queue.release()
        INFERENCE_REQUESTS.labels(outcome="error").inc()
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}")

    if upstream_response.status_code != 200:
        detail = await upstream_response.aread()
        await upstream_stream.__aexit__(None, None, None)
        await client.aclose()
        await admission_queue.release()
        INFERENCE_REQUESTS.labels(outcome="error").inc()
        return Response(
            content=detail,
            status_code=upstream_response.status_code,
            media_type=upstream_response.headers.get("content-type", "text/plain"),
        )

    async def stream_from_ollama() -> AsyncIterator[bytes]:
        first_chunk = True
        try:
            async for chunk in upstream_response.aiter_bytes():
                if first_chunk:
                    # Record TTFT on the first real byte — compare this across
                    # your two nodes in step 2b to confirm routing signals work.
                    TTFT.observe(time.perf_counter() - arrived_at)
                    first_chunk = False
                yield chunk
            INFERENCE_REQUESTS.labels(outcome="success").inc()
        except Exception:
            INFERENCE_REQUESTS.labels(outcome="error").inc()
            raise
        finally:
            TOTAL_LATENCY.observe(time.perf_counter() - arrived_at)
            await upstream_stream.__aexit__(None, None, None)
            await client.aclose()
            await admission_queue.release()

    return StreamingResponse(
        stream_from_ollama(),
        media_type=upstream_response.headers.get("content-type", "text/event-stream"),
    )