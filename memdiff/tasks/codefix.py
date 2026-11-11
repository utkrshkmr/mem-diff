import json
import os
import random
import re
import resource
import subprocess
import sys
import tempfile

from .base import Instance, assign_kinds
from .codebank import BANK

HARNESS = """
import json, sys
src = open(sys.argv[1]).read()
cases = json.load(open(sys.argv[2]))
ns = {}
ok = 0
try:
    exec(compile(src, "sub", "exec"), ns)
    f = ns[sys.argv[3]]
    for args, exp in cases:
        try:
            if json.loads(json.dumps(f(*args))) == exp:
                ok += 1
        except Exception:
            pass
except Exception:
    pass
print(json.dumps({"ok": ok, "n": len(cases)}))
"""


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    os.setsid()


def run_cases(src, fn, cases):
    with tempfile.TemporaryDirectory() as d:
        a, b, h = (os.path.join(d, x) for x in ("s.py", "c.json", "h.py"))
        open(a, "w").write(src)
        json.dump(cases, open(b, "w"))
        open(h, "w").write(HARNESS)
        try:
            r = subprocess.run([sys.executable, "-I", h, a, b, fn], capture_output=True, text=True, timeout=10, cwd=d, preexec_fn=_limits)
            o = json.loads(r.stdout.strip().splitlines()[-1])
            return o["ok"] / max(1, o["n"])
        except Exception:
            return 0.0


_CACHE = {}


def cases_for(task):
    k = task["fn"]
    if k not in _CACHE:
        ns = {}
        exec(task["ref"], ns)
        f = ns[k]
        vis = [[a, json.loads(json.dumps(f(*a)))] for a, _ in task["visible"]]
        hid = [[a, json.loads(json.dumps(f(*a)))] for a, _ in task["hidden"]]
        _CACHE[k] = (vis, hid)
    return _CACHE[k]


def generate(seed, n_items=3):
    rng = random.Random(seed + 7)
    picks = rng.sample(range(len(BANK)), n_items)
    items, pieces = [], []
    for k, b in enumerate(picks):
        t = BANK[b]
        vis, hid = cases_for(t)
        iid = f"f{k + 1}"
        items.append({"id": iid, "prompt": f"Fix function `{t['fn']}` ({iid}) so it meets its specification.", "answer": t["ref"], "fn": t["fn"], "cases": vis + hid})
        pieces.append({"id": f"s{len(pieces) + 1}", "text": f"[{iid}] Current buggy source of {t['fn']}:\n{t['buggy']}", "items": [(iid, 0)]})
        tests = "\n".join(f"assert {t['fn']}({', '.join(json.dumps(x) for x in a)}) == {json.dumps(e)}" for a, e in vis)
        pieces.append({"id": f"s{len(pieces) + 1}", "text": f"[{iid}] Specification: {t['spec']}\nTests that the correct {t['fn']} must pass (JSON literals):\n{tests}", "items": [(iid, 1)]})
    assign_kinds(items)
    return Instance(
        task="codefix",
        seed=seed,
        intro="Fix buggy Python functions. For each function, the buggy source and the specification with tests may be held by different agents. A submission is the complete corrected Python source of the function; it is graded on visible and hidden tests.",
        answer_format="the full corrected Python source of the function (def ...)",
        items=items,
        pieces=pieces,
    )


def extract(ans):
    s = str(ans)
    m = re.search(r"```(?:python)?\n(.*?)```", s, re.S)
    return m.group(1) if m else s


def score(item, answer):
    return run_cases(extract(answer), item["fn"], item["cases"])
