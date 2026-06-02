"""
forwarder.py — Sends a request to the chosen peer and relays its response.

Two cases:
  - Streaming  (stream=true) : relay bytes chunk by chunk as they arrive.
  - Blocking   (stream=false): wait for the full response, return it.

If the chosen peer fails mid-request, the router retries on the next
best peer automatically (see main.py for the retry loop).
"""

import logging
from typing import AsyncIterator

import httpx

log = logging.getLogger("router.forwarder")


async def forward_stream(
    peer_url: str,
    payload: dict,
    api_key: str | None = None,
) -> AsyncIterator[bytes]:
    """
    Open a streaming POST to the peer and yield raw bytes as they arrive.
    The caller (main.py) wraps this in a FastAPI StreamingResponse.

    Raises httpx.RequestError if the peer is unreachable.
    Raises httpx.HTTPStatusError if the peer returns a non-200 status.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    # timeout=None because generation can take a while.
    # The client's own timeout (if any) closes the connection from the other end.
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{peer_url}/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as response:
            if response.status_code != 200:
                body = await response.aread()
                log.error(f"Peer {peer_url} returned {response.status_code}: {body[:200]}")
                # Raise so the retry loop in main.py can try the next peer.
                response.raise_for_status()

            async for chunk in response.aiter_bytes():
                yield chunk


async def forward_blocking(
    peer_url: str,
    payload: dict,
    api_key: str | None = None,
) -> dict:
    """
    Send a non-streaming POST and return the full JSON response.

    Raises httpx.RequestError / httpx.HTTPStatusError on failure
    so the retry loop in main.py can try the next peer.
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{peer_url}/v1/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        return response.json()
