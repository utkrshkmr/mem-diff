import asyncio
import json

from memdiff.episode import run_episode
from memdiff.tasks import generate

ARCH = {"active_params_b": 8.2, "layers": 36, "attn_dim": 4096}


class Oracle:
    def __init__(self, answers, delay_items=0):
        self.cfg = dict(ARCH)
        self.answers = answers
        self.n = 0

    async def chat(self, messages, tools, seed):
        self.n += 1
        names = [t["function"]["name"] for t in tools]
        k = sum(1 for m in messages if m["role"] == "assistant")
        if "submit_answer" in names and k == 1:
            calls = [("submit_answer", {"item_id": i, "answer": a}) for i, a in self.answers.items()]
        elif k == 0:
            w = [n for n in names if n in ("kg_add", "append_line", "kv_put")][:1]
            args = {"kg_add": {"subject": "x", "relation": "works_at", "object": "y"}, "append_line": {"text": "x"}, "kv_put": {"key": "k", "value": "v"}}
            calls = [(w[0], args[w[0]])] if w else [("end_turn", {})]
        else:
            calls = [("end_turn", {})]
        out = [(f"c{self.n}{j}", n, json.dumps(a)) for j, (n, a) in enumerate(calls)]
        msg = {"role": "assistant", "content": "", "tool_calls": [{"id": i, "type": "function", "function": {"name": n, "arguments": a}} for i, n, a in out]}
        return msg, out, {"in": 1000, "out": 50, "cached": 600, "req_s": 0.1, "retries": 0}


def run(mode, backend="kg"):
    inst = generate("factfind", 3)
    ans = {it["id"]: it["answer"] for it in inst.items}
    llms = {"o": Oracle(ans)}
    spec = {"id": "x", "task": "factfind", "mode": mode, "backend": backend, "acl": "open", "schedule": "sequential", "seed": 3, "models": {"A": "o", "B": "o"}}
    return asyncio.run(run_episode(spec, llms, {"rounds": 3, "tools_per_turn": 4}))


def test_cost_to_complete_and_timeline():
    res, _ = run("collab")
    c = res["cost"]
    assert res["score"] == 1.0 and c["completed"]
    assert c["tok_to_complete"] == 2 * 1050
    assert c["tok_total"] >= c["tok_to_complete"]
    assert c["tok_processed"] == c["calls"] * 450
    assert c["flops"] > 0 and c["flops_to_complete"] <= c["flops"]
    tl = res["_timeline"]
    assert tl[-1]["solved"] == 6 and tl[0]["solved"] == 0
    assert all(tl[i]["cum_tok_total"] <= tl[i + 1]["cum_tok_total"] for i in range(len(tl) - 1))
    assert len(res["trajectory"]) == res["rounds_run"]
    assert res["mem"]["records"] >= 1


def test_adversary_share_and_poison():
    res, _ = run("poison", "scratchpad")
    assert res["cost"]["adversary_tok_share"] > 0
    assert res["cost"]["completed"]
