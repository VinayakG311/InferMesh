# 🧠 InferMesh

 

### Distributed AI Inference Scheduler

 

**Route. Balance. Recover. Automatically.**

 

InferMesh is an open-source distributed inference scheduler that sits in front of your LLM nodes and intelligently routes every request to the best available server — based on real-time load, latency, and queue depth. Nodes join and leave the cluster automatically. No Kubernetes required.

 



 </div> 

---

 

## What is InferMesh?

 

Most LLM deployments are single-node: one GPU, one model, one point of failure. When that node is busy or down, requests fail.

 

InferMesh adds a thin scheduling layer between your clients and your inference nodes. Every node runs a lightweight proxy. A router sits in front, continuously monitoring the health and load of each node, and routes each incoming request to the best available one.

 

Nodes discover each other automatically via a UDP gossip protocol — no central registry, no configuration changes when you add or remove a server. If a node dies mid-request, the router retries on the next best node transparently.

 

```
Your App / curl / chat.py
         │
         ▼
   ┌─────────────┐
   │   InferMesh    │  ← single entry point  :9000
   │   Router    │  ← scores & routes every request
   └──────┬──────┘
          │
    ┌─────┴──────┐
    ▼            ▼
┌────────┐  ┌────────┐        ┌──────────────┐
│ Node A │  │ Node B │  ···   │  OpenRouter  │  ← remote spillover
│ :8000  │  │ :8001  │        │  (fallback)  │
└───┬────┘  └───┬────┘        └──────────────┘
    ▼            ▼
 Ollama A    Ollama B
 :11434      :11435

```

 

---

 

## Features

 

### 🔀 Intelligent Request Routing

 

Every request is scored against all live nodes using a weighted function across three real-time signals — latency, queue depth, and active request count. The node with the lowest score wins. Weights are tunable constants; no code changes needed to adjust routing behaviour.

 

```
score = (latency_ms × 1.0) + (queue_depth × 200) + (active_requests × 150)

```

 

### 📡 Gossip-Based Peer Discovery

 

Nodes find each other automatically via UDP broadcast. Start a new node and it joins the cluster within seconds. Kill a node and it's evicted after a configurable timeout. No Consul, no etcd, no central coordinator.

 

### 🔁 Automatic Failover with Retry

 

If a chosen node fails mid-request, the router retries on the next best available node. The client sees no error. If all nodes fail, a clean `503` is returned.

 

### ☁️ OpenRouter Spillover

 

Configure OpenRouter as a remote fallback peer. It receives traffic only when all local nodes are saturated, via a configurable latency penalty in the scoring function. One config change — no code touched.

 

### 📊 Prometheus Metrics Built In

 

Every proxy node exports:

 

- `proxy_inference_requests_total` — request outcomes (success / error / rejected)
- `proxy_time_to_first_token_seconds` — TTFT histogram
- `proxy_request_duration_seconds` — end-to-end latency
- `proxy_queue_depth` — requests waiting for a slot
- `proxy_active_requests` — requests currently running

 

### 🛡️ Admission Control

 

Each node has a bounded request queue. When the queue is full, new requests receive an immediate `429` instead of hanging. Queue size and concurrency limits are configurable per node via environment variables.

 

### 🔌 OpenAI-Compatible API

 

InferMesh exposes the same `/v1/chat/completions` interface as OpenAI. Point any existing OpenAI client at the router and it works without modification.

 

### 🍎 Apple Silicon Native

 

Developed and tested on Apple M-series chips. Uses Ollama with Metal acceleration — no CUDA required. Runs a full multi-node simulation on a single MacBook.

 

---

 

## Architecture

 

```
inference-engine/
│
├── app/                    # Proxy — runs on every node
│   └── main.py             # FastAPI: queue, metrics, health, /node/info
│
├── router/                 # Router — single cluster entry point
│   ├── main.py             # Request intake, retry loop, /peers endpoint
│   ├── peers.py            # Peer registry: discovery + health polling
│   ├── gossip.py           # UDP gossip: announce self, listen for peers
│   ├── scorer.py           # Weighted scoring function
│   ├── forwarder.py        # Forwards requests, relays streams
│   ├── openrouter.py       # OpenRouter peer adapter
│   └── config.yaml         # Cluster configuration
│
├── chat.py                 # Terminal chat UI (for testing)
└── start.sh                # Boots two nodes + router in one command

```

 

**Separation of concerns:** each file has exactly one job. To swap in a different scoring algorithm, touch only `scorer.py`. To replace gossip with a service registry, touch only `peers.py`. The rest of the system is unaffected.

 

---

 

## Quickstart

 

### Prerequisites

 

- Python 3.11+
- [Ollama](https://ollama.com/download/mac) installed
- ~2 GB free disk space for the model

 

### 1 — Install Ollama and pull the model

 

```bash
# Install Ollama (macOS)
curl -fsSL https://ollama.com/install.sh | sh

# Pull the model (only needed once)
ollama pull llama3.2:3b

```

 

### 2 — Clone and install dependencies

 

```bash
git clone https://github.com/your-username/infermesh
cd infermesh
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

```

 

### 3 — Start two Ollama instances (simulates two nodes)

 

```bash
# Terminal 1
OLLAMA_HOST=0.0.0.0:11434 ollama serve

# Terminal 2
OLLAMA_HOST=0.0.0.0:11435 ollama serve

```

 

### 4 — Start the cluster

 

```bash
chmod +x start.sh && ./start.sh

```

 

This boots:

 

- **Node A** — proxy on `:8000`, backed by Ollama on `:11434`
- **Node B** — proxy on `:8001`, backed by Ollama on `:11435`
- **Router** — on `:9000`, routing between both nodes

 

### 5 — Chat

 

```bash
python chat.py

```

 

---

 

## Configuration

 

All cluster settings live in `router/config.yaml`. No code changes needed.

 

```yaml
router:
  port: 9000
  health_check_interval: 5      # seconds between health polls

gossip:
  enabled: true                 # false = use static peer list below
  port: 6000                    # UDP broadcast port
  announce_interval: 4          # how often each node announces itself
  peer_timeout: 15              # seconds of silence before eviction

peers:                          # used only when gossip is disabled
  - id: "node-a"
    url: "http://localhost:8000"
  - id: "node-b"
    url: "http://localhost:8001"

openrouter:
  enabled: false                # true to activate remote spillover
  api_key: ""                   # or set OPENROUTER_API_KEY env var
  model: "meta-llama/llama-3.2-3b-instruct:free"
  base_latency_ms: 800          # scoring penalty to keep local nodes preferred

```

 

### Per-node environment variables

 


| Variable                  | Default                  | Description                     |
| ------------------------- | ------------------------ | ------------------------------- |
| `OLLAMA_URL`              | `http://localhost:11434` | Ollama backend address          |
| `MODEL_NAME`              | `llama3.2:3b`            | Model to serve                  |
| `NODE_ID`                 | random                   | Unique node identifier          |
| `MAX_CONCURRENT_REQUESTS` | `1`                      | Max parallel inference requests |
| `MAX_QUEUE_SIZE`          | `8`                      | Max waiting requests before 429 |


 

### Router environment variables

 


| Variable        | Default                 | Description                                   |
| --------------- | ----------------------- | --------------------------------------------- |
| `NODE_ID`       | random                  | This router's identity (broadcast via gossip) |
| `PROXY_URL`     | `http://localhost:8000` | Proxy address to announce to peers            |
| `ROUTER_CONFIG` | `router/config.yaml`    | Path to config file                           |


 

---

 

## Verify routing is working

 

```bash
# See all peers and their current scores
curl http://localhost:9000/peers

# See which node handled a specific request
curl -s -D - http://localhost:9000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"hello"}],"stream":false}' \
  | grep X-Routed-To

# Check router health
curl http://localhost:9000/health/ready

# Prometheus metrics for a node
curl http://localhost:8000/metrics

```

 

---

 

## Tuning the scoring function

 

Edit the weights in `router/scorer.py`:

 

```python
WEIGHT_LATENCY = 1.0      # ms — penalises slow nodes
WEIGHT_QUEUE   = 200.0    # per waiting request
WEIGHT_ACTIVE  = 150.0    # per active request

```

 

**Examples:**

 

- Prioritise speed above all else → increase `WEIGHT_LATENCY`
- Spread load evenly across nodes → increase `WEIGHT_QUEUE`
- Prefer idle nodes over fast-but-busy ones → increase `WEIGHT_ACTIVE`

 

---

 

## Adding a new node

 

**With gossip enabled** — just start a new proxy instance pointing at a new Ollama backend. It announces itself and joins the cluster within one gossip cycle (~4 seconds). No config changes.

 

```bash
NODE_ID=node-c \
OLLAMA_URL=http://localhost:11436 \
uvicorn app.main:app --port 8002

```

 

**Without gossip** — add an entry to the `peers` list in `config.yaml` and restart the router.

 

---

 

## Roadmap

 

- [x] Single-node proxy with metrics and admission control
- [x] Multi-node simulation on a single machine
- [x] Weighted scoring router with automatic failover
- [x] UDP gossip peer discovery
- [x] OpenRouter remote spillover
- [ ] Router test suite
- [ ] Multi-machine deployment guide (cloud VMs)
- [ ] Grafana dashboard
- [ ] mTLS inter-node authentication
- [ ] KV-cache prefix sharing across nodes
- [ ] Request hedging for P99 latency

 

---

 

## Contributing

 

Contributions are welcome. The codebase is intentionally kept simple — each file has one job and is written to be readable without deep systems knowledge.

 

1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Keep changes focused — one concern per PR
4. Open a pull request with a clear description of what changed and why

 

---

 

## License

 

MIT — use it, fork it, build on it.

 

---

 <div align="center"> Built with Python · FastAPI · Ollama · llama.cpp </div>