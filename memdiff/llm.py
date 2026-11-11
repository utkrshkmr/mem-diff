import asyncio
import json
import random
import re
import time
import uuid

from openai import AsyncOpenAI

TC = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


class LLM:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.client = AsyncOpenAI(base_url=cfg["base_url"], api_key=cfg.get("api_key", "EMPTY"), timeout=cfg.get("timeout", 600), max_retries=0)

    async def chat(self, messages, tools, seed):
        c = self.cfg
        extra = {"top_k": c.get("top_k", 20), "chat_template_kwargs": {"enable_thinking": bool(c.get("thinking", False))}}
        last = None
        t0 = time.perf_counter()
        for attempt in range(6):
            try:
                r = await self.client.chat.completions.create(
                    model=c["served"],
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=c.get("temperature", 0.7),
                    top_p=c.get("top_p", 0.8),
                    max_tokens=c.get("max_tokens", 1536),
                    seed=seed,
                    extra_body=extra,
                )
                break
            except Exception as e:
                last = e
                if "maximum context length" in str(e) or "max_tokens" in str(e):
                    raise
                await asyncio.sleep(min(60, 2 ** attempt + random.random()))
        else:
            raise last
        m = r.choices[0].message
        content = m.content or ""
        calls = []
        for t in m.tool_calls or []:
            calls.append((t.id, t.function.name, t.function.arguments or "{}"))
        if not calls:
            for j in TC.findall(content):
                try:
                    d = json.loads(j)
                    calls.append(("call_" + uuid.uuid4().hex[:12], d.get("name", ""), json.dumps(d.get("arguments", {}))))
                except json.JSONDecodeError:
                    pass
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in calls]
        u = r.usage
        det = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(det, "cached_tokens", 0) if det is not None else 0) or 0
        usage = {"in": getattr(u, "prompt_tokens", 0) or 0, "out": getattr(u, "completion_tokens", 0) or 0, "cached": cached, "req_s": time.perf_counter() - t0, "retries": attempt}
        usage["truncated"] = r.choices[0].finish_reason == "length"
        usage["malformed"] = not calls and "<tool_call>" in content
        return msg, calls, usage


class MockLLM:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg

    async def chat(self, messages, tools, seed):
        k = 0
        for m in reversed(messages):
            if m["role"] == "user":
                break
            if m["role"] == "assistant":
                k += 1
        rng = random.Random(seed * 31 + len(messages))
        names = [t["function"]["name"] for t in tools]
        ev = [l[2:] for l in messages[1]["content"].splitlines() if l.startswith("- ")]
        ids = re.findall(r"^\[(\w+)\]", messages[1]["content"], re.M)

        def fill(t, text):
            a = {}
            for p, s in t["function"]["parameters"]["properties"].items():
                if s["type"] == "string":
                    a[p] = (s.get("enum") or [text])[0]
                elif s["type"] == "array":
                    a[p] = text.split()[:2] or ["x"]
                elif s["type"] == "integer":
                    a[p] = -1
            return a

        calls = []
        writers = [t for t in tools if t["function"]["name"] not in ("submit_answer", "end_turn") and any(w in t["function"]["name"] for w in ("append", "put", "post", "add", "out", "assert", "critique"))]
        readers = [t for t in tools if any(w in t["function"]["name"] for w in ("read", "list", "query", "rd", "view"))]
        if k == 0 and writers:
            t = rng.choice(writers)
            calls.append((t["function"]["name"], fill(t, rng.choice(ev) if ev else "x")))
        elif k == 1 and readers:
            t = rng.choice(readers)
            calls.append((t["function"]["name"], {}))
        elif k == 2 and "submit_answer" in names and ids:
            for i in ids:
                calls.append(("submit_answer", {"item_id": i, "answer": "unknown"}))
        else:
            calls.append(("end_turn", {}))
        out = [("call_" + uuid.uuid4().hex[:12], n, json.dumps(a)) for n, a in calls]
        msg = {"role": "assistant", "content": "", "tool_calls": [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in out]}
        await asyncio.sleep(0)
        n_in = sum(len(str(m.get("content", ""))) + len(json.dumps(m.get("tool_calls", ""))) for m in messages) // 4
        return msg, out, {"in": n_in, "out": 20, "cached": int(n_in * 0.8) if len(messages) > 3 else 0, "req_s": 0.01, "retries": 0, "truncated": False, "malformed": False}


def make_llm(name, cfg):
    return MockLLM(name, cfg) if cfg.get("type") == "mock" else LLM(name, cfg)
