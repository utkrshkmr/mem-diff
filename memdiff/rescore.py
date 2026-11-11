import csv
import gzip
import json
import os
import shutil

from .episode import MODES, summarize_evidence, claim_stats
from .memory import make
from .tasks import generate, allocate, TASKS


def read_tl(path):
    if not os.path.exists(path):
        return None, None
    with gzip.open(path, "rt", newline="") as f:
        r = csv.DictReader(f)
        rows = list(r)
        cols = r.fieldnames
    def num(v):
        for f in (int, float):
            try:
                return f(v)
            except (TypeError, ValueError):
                pass
        return v

    return [{k: num(v) for k, v in row.items()} for row in rows], cols


def write_tl(path, rows):
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with gzip.open(path, "wt", newline="") as g:
        w = csv.DictWriter(g, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def kept_fields(res, subs, n_items):
    team = res["mode"] in ("collab", "zerosum")
    cur, done = {}, None
    for sb in subs:
        if team or sb["agent"] == "A":
            cur[sb["item"]] = bool(sb.get("correct"))
            if done is None and sum(cur.values()) == n_items:
                done = sb
    return done is not None and sum(cur.values()) == n_items


def rescore_one(run_dir, res, trace):
    spec = trace["spec"]
    inst = generate(spec["task"], spec["seed"])
    n = len(inst.items)
    res.setdefault("cost", {})["still_correct_at_end"] = kept_fields(res, trace["submissions"], n)
    if spec["task"] != "factfind":
        return res, None, None, "ok"
    mod = TASKS["factfind"]
    mode = spec["mode"]
    alloc = allocate(inst, "solo" if mode == "solo" else "critic" if mode == "critic" else "split")
    ids = [a for a in ("A", "B") if MODES[mode][a]]
    m = make(spec["backend"], acl=spec["acl"], schema=inst.schema if spec["backend"] == "kg" else None, functional=inst.functional)
    m.agents = list(ids)
    tr = mod.Tracker(inst, alloc)
    tl, _ = read_tl(os.path.join(run_dir, "timelines", res["id"] + ".csv.gz"))
    det_p = os.path.join(run_dir, "details", res["id"] + ".json.gz")
    det = json.load(gzip.open(det_p, "rt")) if os.path.exists(det_p) else {}
    traj = det.get("trajectory") or res.get("trajectory") or []
    ops = trace["oplog"]
    staged = spec["schedule"] == "simultaneous"
    status = "ok"

    def apply(e):
        if e["kind"] == "write" and e["ok"]:
            r = m.apply(e["op"], e["args"], e["agent"], e["round"], e["role"])
            if not r.get("ok"):
                return False
        return True

    rounds = sorted({int(r["round"]) for r in tl}) if tl else []
    by_round_ev = {}
    if tl:
        p = 0
        writes_by_round = {}
        if staged:
            for e in ops:
                if e["kind"] != "read":
                    writes_by_round.setdefault(int(e["round"]), []).append(e)
        for k, row in enumerate(tl):
            rnd = int(row["round"])
            if not staged:
                n_ops = int(row.get("mem_reads", 0) or 0) + int(row.get("mem_writes", 0) or 0) + int(row.get("mem_denied", 0) or 0)
                for e in ops[p:p + n_ops]:
                    if e["agent"] != row["agent"]:
                        status = "misaligned"
                    if not apply(e):
                        status = "replay_fail"
                p += n_ops
            ev = tr.update(m.records(), {"i": int(row["i"]), "tok": row["cum_tok_total"], "round": rnd})
            row.update(ev)
            last_in_round = k == len(tl) - 1 or int(tl[k + 1]["round"]) != rnd
            if last_in_round:
                if staged:
                    for e in writes_by_round.get(rnd, []):
                        if not apply(e):
                            status = "replay_fail"
                by_round_ev[rnd] = tr.update(m.records(), {"i": int(row["i"]), "tok": row["cum_tok_total"], "round": rnd})
        if not staged and p != len(ops):
            status = "misaligned"
    if m.state_hash() != res.get("state_hash"):
        status = "hash_mismatch" if status == "ok" else status
    for t in traj:
        if int(t["round"]) in by_round_ev:
            t.update(by_round_ev[int(t["round"])])
    subs = trace["submissions"]
    team = mode in ("collab", "zerosum")
    a_cur, t_cur, a_first = {}, {}, {}
    for sb in subs:
        ok = bool(sb.get("correct"))
        t_cur[sb["item"]] = ok
        if sb["agent"] == "A":
            a_cur[sb["item"]] = ok
            if ok and sb["item"] not in a_first:
                a_first[sb["item"]] = {"tok": sb["cum_tok"], "round": sb["round"], "i": sb["call"], "flops": sb["cum_flops"]}
    final_ok = {it["id"]: (t_cur if team else a_cur).get(it["id"], False) for it in inst.items}
    end = {"tok": res.get("cost", {}).get("tok_total") or (tl[-1]["cum_tok_total"] if tl else 0), "round": res.get("rounds_run", 0)}
    links, items, summ = summarize_evidence(tr, final_ok, a_first, end)
    res["evidence"] = summ
    cper, cmem = claim_stats(mod, inst, m.records())
    res.setdefault("mem", {}).update(cmem)
    for a, st in (res.get("agents") or {}).items():
        st["true_pairs"], st["false_pairs"] = cper.get(a, [0, 0])
    det.update({"trajectory": traj, "links": links, "evidence_items": items})
    return res, det, tl, status


def rescore(run_dir):
    ep = os.path.join(run_dir, "episodes.jsonl")
    rows = {}
    for line in open(ep):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r["id"] not in rows or not r.get("failed"):
            rows[r["id"]] = r
    stats = {}
    out = []
    for i, r in rows.items():
        tp = os.path.join(run_dir, "traces", i + ".json.gz")
        if r.get("failed") or not os.path.exists(tp):
            out.append(r)
            stats["skipped"] = stats.get("skipped", 0) + 1
            continue
        trace = json.load(gzip.open(tp, "rt"))
        r, det, tl, status = rescore_one(run_dir, r, trace)
        stats[status] = stats.get(status, 0) + 1
        if det is not None:
            with gzip.open(os.path.join(run_dir, "details", i + ".json.gz"), "wt") as g:
                json.dump(det, g)
        if tl:
            write_tl(os.path.join(run_dir, "timelines", i + ".csv.gz"), tl)
        out.append(r)
    tmp = ep + ".tmp"
    with open(tmp, "w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    shutil.move(tmp, ep)
    print("[rescore]", run_dir, stats)
    return stats
