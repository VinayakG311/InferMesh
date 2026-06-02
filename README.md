<div align="center">

<br/>

<img src="https://img.shields.io/badge/InferMesh-Distributed%20AI%20Inference%20Scheduler-6366f1?style=for-the-badge&labelColor=0f0f0f" />

<br/><br/>

**Route intelligently. Recover automatically. Scale without friction.**

*An open-source distributed inference scheduler for self-hosted LLMs.*

<br/>

[![Python](https://img.shields.io/badge/Python_3.11+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![OpenAI Compatible](https://img.shields.io/badge/OpenAI_API-Compatible-412991?style=flat-square&logo=openai&logoColor=white)](https://platform.openai.com/docs/api-reference)
[![License: MIT](https://img.shields.io/badge/License-MIT-f59e0b?style=flat-square)](LICENSE)

<br/>

</div>

---

## The Problem

Self-hosted LLM deployments are single points of failure. One node, one model, one GPU. When it's busy — requests queue. When it's down — requests fail. Scaling means manually load balancing, manually tracking node health, manually handling failover.

**InferMesh eliminates all of that.**

---

## How It Works

```
  Your App  ──►  InferMesh Router  ──►  Best Available Node
                      │
               scores every node
               on every request
                      │
              ┌───────┼───────┐
              ▼       ▼       ▼
           Node A   Node B  OpenRouter
           (local)  (local)  (fallback)
```

A lightweight proxy runs on each inference node. The router sits in front, continuously monitoring every node's health and load. Every incoming request is scored in real time — the best node wins. No manual intervention. No single point of failure.

---

## Features

<br/>

### 📡 &nbsp; Zero-Config Peer Discovery

Nodes find each other over UDP gossip broadcast. Start a new node and it joins the mesh within seconds. Kill a node and it's evicted automatically. No service registry. No Consul. No etcd. No config changes.

<br/>

### ⚡ &nbsp; Real-Time Intelligent Routing

Every request is scored against all live nodes across three signals simultaneously:

```
score  =  ( latency_ms × 1.0 )  +  ( queue_depth × 200 )  +  ( active_requests × 150 )
```

The node with the lowest score serves the request. Weights are fully tunable — bias toward speed, load distribution, or a balance of both.

<br/>

### 🔁 &nbsp; Automatic Failover

If a chosen node fails mid-request, InferMesh retries on the next best available node transparently. The client never sees an error. Only when every node in the cluster fails does the router return a `503`.

<br/>

### ☁️ &nbsp; Cloud Spillover via OpenRouter

Configure OpenRouter as a remote fallback peer. It receives traffic only when all local nodes are saturated — enforced via a configurable latency penalty in the scoring function. One config line. No code touched.

<br/>

### 🛡️ &nbsp; Admission Control

Each node runs a bounded admission queue. Requests that exceed node capacity receive an immediate `429` instead of hanging indefinitely. Queue size and concurrency limits are set per-node via environment variables.

<br/>

### 📊 &nbsp; Prometheus Metrics on Every Node

| Metric | Description |
|--------|-------------|
| `proxy_inference_requests_total` | Request outcomes — success, error, rejected |
| `proxy_time_to_first_token_seconds` | TTFT histogram per node |
| `proxy_request_duration_seconds` | Full end-to-end latency |
| `proxy_queue_depth` | Live requests waiting for a slot |
| `proxy_active_requests` | Live requests currently running |

<br/>

### 🔌 &nbsp; Drop-In OpenAI API Compatibility

InferMesh exposes the standard `/v1/chat/completions` interface. Any existing OpenAI client points at the router with zero modification. Streaming and blocking modes both supported.

<br/>

---

## Architecture

```
inference-engine/
│
├── app/                    # Proxy — one instance per node
│   └── main.py             # Queue · Metrics · Health · Node info
│
└── router/                 # Scheduler — single cluster entry point
    ├── peers.py            # Peer registry — discovery + health polling
    ├── gossip.py           # UDP gossip — announce · listen · evict
    ├── scorer.py           # Weighted scoring — picks the best node
    ├── forwarder.py        # Request forwarding + stream relay
    ├── openrouter.py       # Remote spillover adapter
    └── config.yaml         # Single file controls the entire cluster
```

Each component has exactly one responsibility. Swap the scoring algorithm without touching discovery. Replace gossip with a service registry without touching the router. The separation is intentional and maintained.

---

## Designed For

- Teams running **self-hosted LLMs** who need resilience without Kubernetes overhead
- **AI infrastructure engineers** building on top of Ollama, llama.cpp, or vLLM
- **Researchers and hobbyists** who want production-grade routing on consumer hardware
- Anyone who wants to stop babysitting GPU nodes

---

## Roadmap

| Status | Item |
|--------|------|
| ✅ | Weighted multi-signal scoring router |
| ✅ | UDP gossip peer discovery |
| ✅ | Automatic failover with retry |
| ✅ | OpenRouter remote spillover |
| ✅ | Prometheus metrics per node |
| ✅ | OpenAI-compatible API surface |
| ⬜ | Grafana dashboard |
| ⬜ | Multi-machine deployment (cloud VMs) |
| ⬜ | mTLS inter-node authentication |
| ⬜ | KV-cache prefix sharing |
| ⬜ | Request hedging for P99 latency |

---

<div align="center">

<br/>

**MIT Licensed &nbsp;·&nbsp; Built with Python, FastAPI, and Ollama**

*Contributions welcome — one concern per PR.*

<br/>

</div>