# Distributed Inference Engine — Single Node Baseline

A local AI inference setup running **Llama 3.2 3B** on your MacBook M4
via Ollama, with a simple chat UI to interact with it.

---

## Prerequisites

- macOS with Apple Silicon (M1/M2/M3/M4)
- Python 3.11 or higher
- ~3 GB of free disk space for the model

Check your Python version:

```bash
python3 --version
```

---

## Step 1 — Install Ollama

Ollama is the tool that downloads and runs the LLM locally using Metal (Apple GPU).

```bash
# Download and install Ollama
curl -fsSL https://ollama.com/install.sh | sh
```

Or download the macOS app directly from [https://ollama.com/download/mac](https://ollama.com/download/mac) and drag it to Applications.

Verify it installed correctly:

```bash
ollama --version
```

---

## Step 2 — Download the Model

Pull Llama 3.2 3B (quantized to 4-bit — uses ~2 GB of memory):

```bash
ollama pull llama3.2:3b
```

This will take a few minutes depending on your internet speed.
You only need to do this once — the model is cached locally after the first pull.

Verify the model is available:

```bash
ollama list
```

You should see `llama3.2:3b` in the output.

---

## Step 3 — Start Ollama

Start the Ollama server in the background:

```bash
ollama serve
```

Keep this terminal open (or run it in the background).
Ollama listens on `http://localhost:11434` by default.

To test that it's working:

```bash
curl http://localhost:11434/api/tags
```

You should see a JSON response listing your downloaded models.

---

## Step 4 — Set Up the Python Environment

Navigate to this project folder and create a virtual environment:

```bash
cd inference-engine

# Create a virtual environment
python3 -m venv venv

# Activate it
source venv/bin/activate
```

Install the required Python packages:

```bash
pip install -r requirements.txt
```

---

## Step 5 — Start the FastAPI Proxy

Start the proxy in a second terminal while Ollama is still running:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The proxy listens on `http://localhost:8000` and forwards chat requests to
Ollama on `http://localhost:11434`.

Useful proxy endpoints:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/health/ready
curl http://localhost:8000/node/info
curl http://localhost:8000/metrics
```

The proxy has a small request queue in front of Ollama. If the queue is full,
it returns `429 Proxy overloaded` so a routing layer can try another node later.

---

## Step 6 — Run the Chat UI

```bash
python chat.py
```

A chat interface will open in your terminal.
Type your message and press Enter to chat with the model.
Type `exit` or `quit` to stop.

---

## Project Structure

```
inference-engine/
├── README.md           # This file
├── requirements.txt    # Python dependencies
├── chat.py             # Terminal chat UI (start here)
└── app/
    ├── __init__.py
    └── main.py         # FastAPI proxy server
```

---

## Troubleshooting

`**ollama: command not found**`
Restart your terminal after installing Ollama, or run:

```bash
export PATH=$PATH:/usr/local/bin
```

`**Error: model not found**`
Run `ollama pull llama3.2:3b` again and wait for it to complete fully.

`**Connection refused` on port 11434**
Ollama isn't running. Open a new terminal and run `ollama serve`.

`**Connection refused` on port 8000**
The proxy isn't running. Open a new terminal and run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**Model responds very slowly**
Make sure no other heavy apps (browser, Xcode) are competing for memory.
The model needs ~2 GB of unified memory to run smoothly.

`**python3: command not found`**
Install Python from [https://www.python.org/downloads/macos/](https://www.python.org/downloads/macos/)

---

## What's Next

Once this proxy setup is working, the next step is to:

1. Run two proxy instances on different ports to simulate two nodes
2. Build the routing layer that polls `/health/ready` and `/node/info`
3. Score nodes using queue depth, latency, and request metrics

Stay on this step until the chat UI works smoothly end-to-end.
