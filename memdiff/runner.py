import asyncio
import csv
import gzip
import hashlib
import itertools
import json
import os
import random
import sys
import time

import yaml

from .episode import run_episode, MODES
from .llm import make_llm


def load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def expand(cfg):
    specs, seen = [], set()
    for b in cfg["blocks"]:
        modes = b["modes"]
        for task, mode, backend, acl, sched, pair, seed in itertools.product(b["tasks"], modes, b["backends"], b.get("acl", ["open"]), b.get("schedule", ["sequential"]), b["pairs"], b["seeds"]):
            if mode == "solo":
                pair = [pair[0], None]
                if acl != "open" or sched != "sequential":
                    continue
            models = {"A": pair[0]}
            if MODES[mode]["B"]:
                models["B"] = pair[1] or pair[0]
            key = f"{task}|{mode}|{backend}|{acl}|{sched}|{models['A']}|{models.get('B')}|{seed}"
            sid = hashlib.sha1(key.encode()).hexdigest()[:14]
            if sid in seen:
                continue
            seen.add(sid)
            specs.append({"id": sid, "task": task, "mode": mode, "backend": backend, "acl": acl, "schedule": sched, "models": models, "seed": int(seed), "block": b["name"]})
    random.Random(0).shuffle(specs)
    return specs


def done_ids(path):
    out = set()
    if os.path.exists(path):
        for line in open(path):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not r.get("failed"):
                out.add(r["id"])
    return out


def used_models(cfg):
    s = set()
    for sp in expand(cfg):
        s |= set(v for v in sp["models"].values() if v)
    return sorted(s)


async def main(cfg_path, limit=None):
    cfg = load(cfg_path)
    out = os.path.join(cfg.get("out", "results"), cfg["run"])
    os.makedirs(os.path.join(out, "traces"), exist_ok=True)
    os.makedirs(os.path.join(out, "timelines"), exist_ok=True)
    epath = os.path.join(out, "episodes.jsonl")
    specs = expand(cfg)
    done = done_ids(epath)
    todo = [s for s in specs if s["id"] not in done]
    if limit:
        todo = todo[:limit]
    json.dump({"config": cfg, "n_specs": len(specs), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}, open(os.path.join(out, "run_meta.json"), "w"), indent=1)
    print(f"[run] {cfg['run']}: {len(specs)} episodes total, {len(done)} done, {len(todo)} to run", flush=True)
    llms = {m: make_llm(m, cfg["models"][m]) for m in used_models(cfg)}
    sem = asyncio.Semaphore(cfg.get("concurrency", 64))
    lock = asyncio.Lock()
    t0 = time.time()
    n = {"ok": 0, "fail": 0}
    sample = float(cfg.get("trace_sample", 1.0))

    async def one(spec):
        async with sem:
            try:
                res, trace = await run_episode(spec, llms, cfg)
            except Exception as e:
                res, trace = {"id": spec["id"], **{k: spec[k] for k in ("task", "mode", "backend", "acl", "schedule", "seed")}, "model_A": spec["models"]["A"], "model_B": spec["models"].get("B"), "failed": f"crash {type(e).__name__}: {str(e)[:300]}"}, None
            res["block"] = spec["block"]
            tl = res.pop("_timeline", None)
            async with lock:
                if tl:
                    cols = list(dict.fromkeys(k for r in tl for k in r))
                    with gzip.open(os.path.join(out, "timelines", spec["id"] + ".csv.gz"), "wt", newline="") as g:
                        w = csv.DictWriter(g, fieldnames=cols)
                        w.writeheader()
                        w.writerows(tl)
                with open(epath, "a") as f:
                    f.write(json.dumps(res) + "\n")
                if trace is not None and int(spec["id"], 16) % 1000 < sample * 1000:
                    with gzip.open(os.path.join(out, "traces", spec["id"] + ".json.gz"), "wt") as g:
                        json.dump(trace, g)
                n["fail" if res.get("failed") else "ok"] += 1
                k = n["ok"] + n["fail"]
                if k % max(1, cfg.get("progress_every", 25)) == 0 or k == len(todo):
                    el = time.time() - t0
                    eta = el / k * (len(todo) - k)
                    print(f"[run] {k}/{len(todo)} ok={n['ok']} fail={n['fail']} elapsed={el / 3600:.2f}h eta={eta / 3600:.2f}h", flush=True)

    await asyncio.gather(*(one(s) for s in todo))
    print(f"[run] finished in {(time.time() - t0) / 3600:.2f}h ok={n['ok']} fail={n['fail']}", flush=True)
    return n
