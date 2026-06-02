"""
scorer.py — Picks the best peer for an incoming request.

The scoring function is a weighted sum of three signals:
  - latency_ms     : last measured round-trip to the peer (lower = better)
  - queue_depth    : requests waiting in the peer's admission queue
  - active_requests: requests currently running on the peer

Lower score = better peer. The peer with the lowest score wins.

Weights are tunable constants at the top of this file.
You don't need to touch anything else to adjust routing behaviour.
"""

import logging
from router.peers import Peer

log = logging.getLogger("router.scorer")

# ── Tunable weights ───────────────────────────────────────────────────────────
# Increase a weight to make the scorer care more about that signal.
# Example: double WEIGHT_LATENCY to strongly prefer the closest node.

WEIGHT_LATENCY = 1.0          # ms — penalises slow peers
WEIGHT_QUEUE = 200.0          # per waiting request — penalises backlogged peers
WEIGHT_ACTIVE = 150.0         # per active request — penalises busy peers


def score(peer: Peer) -> float:
    """
    Compute a single numeric score for one peer.
    Lower is better — the router picks the peer with the minimum score.
    """
    return (
        WEIGHT_LATENCY * peer.latency_ms
        + WEIGHT_QUEUE * peer.queue_depth
        + WEIGHT_ACTIVE * peer.active_requests
    )


def pick(peers: list[Peer]) -> Peer | None:
    """
    Given a list of alive peers, return the one with the lowest score.
    Returns None if the list is empty (all peers are dead).
    """
    if not peers:
        return None

    chosen = min(peers, key=score)

    # Log the scores so you can see why the router made its decision.
    for p in peers:
        marker = " ← chosen" if p is chosen else ""
        log.debug(
            f"  {p.id:12s} score={score(p):7.1f}  "
            f"latency={p.latency_ms:.0f}ms  "
            f"queue={p.queue_depth}  "
            f"active={p.active_requests}{marker}"
        )

    return chosen
