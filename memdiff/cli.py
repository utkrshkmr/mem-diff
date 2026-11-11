import argparse
import asyncio
import collections
import json
import sys

from . import runner
from .llm import make_llm
from .memory.base import tool, P


def cmd_plan(a):
    cfg = runner.load(a.config)
    specs = runner.expand(cfg)
    c = collections.Counter((s["block"], s["task"], s["mode"]) for s in specs)
    for k, v in sorted(c.items()):
        print(*k, v)
    print("total", len(specs))
    print("models", " ".join(runner.used_models(cfg)))


def cmd_models(a):
    cfg = runner.load(a.config)
    for m in runner.used_models(cfg):
        c = cfg["models"][m]
        if c.get("type") == "mock":
            continue
        print(m, c["hf"], c["served"], c["port"], c.get("gpu_util", 0.3), c.get("max_model_len", 32768), c.get("extra_args", ""))


async def _preflight(cfg):
    t = [tool("record", "Record a number.", {"value": P("integer", "the number")}, ["value"])]
    bad = 0
    for m in runner.used_models(cfg):
        if cfg["models"][m].get("type") == "mock":
            continue
        llm = make_llm(m, cfg["models"][m])
        try:
            msg, calls, u = await llm.chat([{"role": "system", "content": "Use tools."}, {"role": "user", "content": "Record the number 7 using the tool."}], t, seed=1)
            ok = any(n == "record" for _, n, _ in calls)
            print(f"[preflight] {m}: {'ok' if ok else 'NO TOOL CALL'} {calls[:1]} usage={u}", flush=True)
            bad += not ok
        except Exception as e:
            print(f"[preflight] {m}: ERROR {e}", flush=True)
            bad += 1
    return bad


def cmd_preflight(a):
    sys.exit(1 if asyncio.run(_preflight(runner.load(a.config))) else 0)


def cmd_run(a):
    n = asyncio.run(runner.main(a.config, a.limit))
    sys.exit(0)


def cmd_analyze(a):
    from .analyze import analyze
    analyze(a.run_dir)


def main():
    ap = argparse.ArgumentParser(prog="memdiff")
    sp = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("models", cmd_models), ("preflight", cmd_preflight), ("run", cmd_run)):
        p = sp.add_parser(name)
        p.add_argument("config")
        if name == "run":
            p.add_argument("--limit", type=int, default=None)
        p.set_defaults(fn=fn)
    p = sp.add_parser("analyze")
    p.add_argument("run_dir")
    p.set_defaults(fn=cmd_analyze)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
