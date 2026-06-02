"""
chat.py — Terminal chat interface for your local Llama 3.2 3B model.

This script talks to the local FastAPI proxy, which forwards requests to Ollama.
Make sure Ollama is running before you start this:
    ollama serve

Then start the proxy in another terminal:
    uvicorn app.main:app --host 0.0.0.0 --port 8000

And that the model is downloaded:
    ollama pull llama3.2:3b

Run this file with:
    python chat.py
"""

import httpx
import json

# ── Config ────────────────────────────────────────────────────────────────────

OLLAMA_URL = "http://localhost:9000"    # where our FastAPI proxy listens
MODEL_NAME = "llama3.2:3b"             # the model we pulled
SYSTEM_PROMPT = "You are a helpful assistant."  # sets the model's behaviour

# ── Helpers ───────────────────────────────────────────────────────────────────

def check_proxy_running():
    """
    Ping the proxy before starting the chat loop.
    If it's not running, give the user a clear error instead of a confusing crash.
    """
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        response.raise_for_status()
    except httpx.ConnectError:
        print("\n[Error] Cannot connect to the FastAPI proxy.")
        print("  → Open a new terminal and run:")
        print("    uvicorn app.main:app --host 0.0.0.0 --port 8000")
        print("  → Make sure Ollama is also running: ollama serve")
        print("  → Then come back and run this script again.\n")
        exit(1)
    except Exception as e:
        print(f"\n[Error] Unexpected problem connecting to the proxy: {e}\n")
        exit(1)


def check_proxy_ready():
    """
    Ask the proxy if it can reach Ollama and see the configured model.
    This catches routing problems before the first chat request.
    """
    try:
        response = httpx.get(f"{OLLAMA_URL}/health/ready", timeout=3)
        response.raise_for_status()
    except httpx.HTTPStatusError:
        print("\n[Error] Proxy is running, but it is not ready.")
        print("  → Open a new terminal and run: ollama serve")
        print(f"  → Make sure the model is downloaded: ollama pull {MODEL_NAME}")
        print("  → Then come back and run this script again.\n")
        exit(1)
    except Exception as e:
        print(f"\n[Error] Unexpected readiness problem: {e}\n")
        exit(1)


def check_model_available():
    """
    Check that our specific model has been downloaded.
    Ollama can be running but the model might not be pulled yet.
    """
    response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=3)
    models = response.json().get("models", [])
    model_names = [m["name"] for m in models]

    # model names in Ollama look like "llama3.2:3b" — check for partial match too
    is_available = any(MODEL_NAME in name for name in model_names)

    if not is_available:
        print(f"\n[Error] Model '{MODEL_NAME}' is not downloaded yet.")
        print(f"  → Run: ollama pull {MODEL_NAME}")
        print(f"  → Then come back and run this script again.\n")
        exit(1)


def stream_response(conversation_history: list) -> str:
    """
    Send the full conversation history to Ollama and stream the response
    back token by token, printing each token as it arrives.

    Streaming means you see words appear gradually — just like ChatGPT —
    instead of waiting for the entire response to finish.

    Returns the full response text when done (so we can add it to history).
    """
    payload = {
        "model": MODEL_NAME,
        "messages": conversation_history,
        "stream": True,          # ask Ollama to send tokens one by one
    }

    full_response = ""

    # httpx.stream keeps the connection open and reads chunks as they arrive
    with httpx.stream("POST", f"{OLLAMA_URL}/v1/chat/completions", json=payload, timeout=60) as response:

        if response.status_code != 200:
            print(f"\n[Error] Proxy returned status {response.status_code}")
            return ""

        for line in response.iter_lines():

            # Ollama sends lines like: data: {"choices": [{"delta": {"content": "Hello"}}]}
            # Empty lines are just heartbeats — skip them
            if not line or line == "data: [DONE]":
                continue

            # Strip the "data: " prefix to get the JSON part
            if line.startswith("data: "):
                json_str = line[len("data: "):]
            else:
                continue

            try:
                chunk = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            # Pull the actual text token out of the nested structure
            token = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")

            if token:
                print(token, end="", flush=True)  # print without newline, immediately
                full_response += token

    print()  # newline after the response finishes
    return full_response


# ── Main chat loop ────────────────────────────────────────────────────────────

def main():
    print("\n── Local Inference Engine ──")
    print(f"Model : {MODEL_NAME}")
    print(f"Host  : {OLLAMA_URL}")
    print("Type 'exit' or 'quit' to stop.\n")

    # Run startup checks before entering the chat loop
    # check_proxy_running()
    # check_proxy_ready()
    # check_model_available()

    print("Ready. Start chatting!\n")
    print("─" * 40)

    # conversation_history holds every message sent and received.
    # We send the full history on every request so the model has context
    # of what was said before — this is how "memory" works in LLM chat.
    conversation_history = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]

    while True:
        # Get input from the user
        try:
            user_input = input("\nYou: ").strip()
        except (KeyboardInterrupt, EOFError):
            # Ctrl+C or Ctrl+D — exit cleanly
            print("\n\nGoodbye!\n")
            break

        # Allow the user to quit by typing a word
        if user_input.lower() in ("exit", "quit", "q"):
            print("\nGoodbye!\n")
            break

        # Don't send empty messages
        if not user_input:
            continue

        # Add the user's message to history before sending
        conversation_history.append({
            "role": "user",
            "content": user_input
        })

        # Print the assistant label, then stream the response inline
        print("\nAssistant: ", end="", flush=True)
        assistant_reply = stream_response(conversation_history)

        if not assistant_reply:
            # Something went wrong — remove the user message we just added
            # so the broken exchange doesn't corrupt future context
            conversation_history.pop()
            print("[No response received. Try again.]\n")
            continue

        # Add the model's reply to history so future messages have full context
        conversation_history.append({
            "role": "assistant",
            "content": assistant_reply
        })


if __name__ == "__main__":
    main()
