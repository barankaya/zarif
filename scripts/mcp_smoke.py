"""MCP smoke test: speaks JSON-RPC over stdio to `zarif serve`.

Verifies: initialize handshake, tools/list (4 zarif tools), and a
zarif_discard_edit round-trip (no LLM needed). Usage: python scripts/mcp_smoke.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

SERVER = [sys.executable, "-c", "from zarif.cli import main; main()", "serve"]


def main() -> int:
    proc = subprocess.Popen(
        SERVER,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert proc.stdin and proc.stdout

    responses: dict[int, dict] = {}
    done = threading.Event()

    def reader() -> None:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                responses[msg["id"]] = msg
                if len(responses) >= 3:
                    done.set()

    threading.Thread(target=reader, daemon=True).start()

    def send(payload: dict) -> None:
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    failures: list[str] = []

    # 1) initialize
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"},
    }})
    init = None
    for _ in range(50):
        if 1 in responses:
            init = responses[1]
            break
        time.sleep(0.1)
    if init is None or "error" in init:
        failures.append(f"initialize failed: {init}")
        proc.kill()
        print("\n".join(failures))
        return 1
    server_info = init["result"]["serverInfo"]
    print(f"[ok] initialize -> serverInfo={server_info}")
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    # 2) tools/list
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = None
    for _ in range(50):
        if 2 in responses:
            tools = responses[2]
            break
        time.sleep(0.1)
    if tools is None or "error" in tools:
        failures.append(f"tools/list failed: {tools}")
        proc.kill()
        print("\n".join(failures))
        return 1
    names = [t["name"] for t in tools["result"]["tools"]]
    print(f"[ok] tools/list -> {names}")
    expected = {"zarif_propose_edit", "zarif_apply_edit", "zarif_discard_edit", "zarif_restore_edit"}
    missing = expected - set(names)
    if missing:
        failures.append(f"missing tools: {missing}")

    # 3) zarif_discard_edit round-trip (no LLM involved)
    send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "zarif_discard_edit", "arguments": {"session_id": "does-not-exist"},
    }})
    discard = None
    for _ in range(50):
        if 3 in responses:
            discard = responses[3]
            break
        time.sleep(0.1)
    if discard is None or "error" in discard:
        failures.append(f"zarif_discard_edit failed: {discard}")
    else:
        print(f"[ok] zarif_discard_edit -> {discard['result']}")

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    if failures:
        print("FAILURES:")
        print("\n".join(failures))
        return 1
    print("MCP SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
