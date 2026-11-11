import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from memdiff.episode import run_episode
from memdiff.llm import LLM

STATE = {"n": 0, "style": "native"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["tools"] and body["chat_template_kwargs"]["enable_thinking"] is False
        STATE["n"] += 1
        last = body["messages"][-1]
        if last["role"] == "user":
            calls = [("kg_add", {"subject": "a", "relation": "works_at", "object": "b"}), ("submit_answer", {"item_id": "q1", "answer": "x"})]
        else:
            calls = [("end_turn", {})]
        if STATE["style"] == "native":
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{STATE['n']}{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}} for i, (n, a) in enumerate(calls)]}
        else:
            msg = {"role": "assistant", "content": "".join(f"<tool_call>{json.dumps({'name': n, 'arguments': a})}</tool_call>" for n, a in calls)}
        out = {"id": "x", "object": "chat.completion", "created": 0, "model": "m", "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_episode_over_http():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cfg = {"base_url": f"http://127.0.0.1:{srv.server_port}/v1", "served": "m"}
    for style in ("native", "hermes"):
        STATE["style"] = style
        llm = LLM("m", cfg)
        spec = {"id": "t", "task": "factfind", "mode": "collab", "backend": "kg", "acl": "open", "schedule": "sequential", "seed": 0, "models": {"A": "m", "B": "m"}}
        res, trace = asyncio.run(run_episode(spec, {"m": llm}, {"rounds": 2, "tools_per_turn": 4}))
        assert res["failed"] is None, res["failed"]
        assert res["agents"]["A"]["submissions"] >= 1 and res["agents"]["A"]["writes"] >= 1
        assert res["replay_ok"]
    srv.shutdown()
