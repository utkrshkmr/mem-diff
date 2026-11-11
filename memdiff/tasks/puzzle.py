import ast
import itertools
import json
import random
import re

from .base import Instance, assign_kinds

N = 5


def holds(c, perm):
    t = c[0]
    if t == "before":
        return perm[c[1]] < perm[c[2]]
    if t == "imm":
        return perm[c[2]] - perm[c[1]] == 1
    if t == "at":
        return perm[c[1]] == c[2]
    if t == "not_at":
        return perm[c[1]] != c[2]
    if t == "apart":
        return abs(perm[c[1]] - perm[c[2]]) > 1
    raise ValueError(t)


def text(c, tag):
    m = lambda i: f"{tag}{i + 1}"
    t = c[0]
    if t == "before":
        return f"{m(c[1])} happens before {m(c[2])}."
    if t == "imm":
        return f"{m(c[2])} happens immediately after {m(c[1])}."
    if t == "at":
        return f"{m(c[1])} is in slot {c[2]}."
    if t == "not_at":
        return f"{m(c[1])} is not in slot {c[2]}."
    return f"{m(c[1])} and {m(c[2])} are not in adjacent slots."


def make_puzzle(rng):
    perm = list(range(1, N + 1))
    rng.shuffle(perm)
    alls = list(itertools.permutations(range(1, N + 1)))
    cons = []
    live = alls
    while len(live) > 1:
        t = rng.choice(["before", "before", "imm", "not_at", "apart", "at"])
        i, j = rng.sample(range(N), 2)
        c = (t, i, j) if t in ("before", "imm", "apart") else (t, i, rng.randint(1, N))
        if not holds(c, perm):
            continue
        nl = [p for p in live if holds(c, p)]
        if len(nl) < len(live):
            cons.append(c)
            live = nl
    while len(cons) < 6:
        i, j = rng.sample(range(N), 2)
        c = ("before", i, j) if perm[i] < perm[j] else ("before", j, i)
        if c not in cons:
            cons.append(c)
    rng.shuffle(cons)
    return perm, cons


def generate(seed, n_items=3):
    rng = random.Random(seed + 101)
    items, pieces = [], []
    tags = ["M", "T", "S"]
    for k in range(n_items):
        perm, cons = make_puzzle(rng)
        tag = tags[k]
        iid = f"p{k + 1}"
        items.append({"id": iid, "prompt": f"Puzzle {iid}: assign events {', '.join(f'{tag}{i + 1}' for i in range(N))} to distinct slots 1..{N} so that every constraint of puzzle {iid} holds.", "answer": {f"{tag}{i + 1}": perm[i] for i in range(N)}, "constraints": [list(c) for c in cons], "tag": tag})
        for h, c in enumerate(cons):
            pieces.append({"id": f"c{len(pieces) + 1}", "text": f"[{iid}] " + text(c, tag), "items": [(iid, h)]})
    assign_kinds(items)
    rng.shuffle(pieces)
    return Instance(
        task="puzzle",
        seed=seed,
        intro=f"Solve ordering puzzles. Each puzzle has {N} events and {N} slots; every event gets a distinct slot. Constraints are split as private evidence; the solution is unique only when all constraints of a puzzle are known.",
        answer_format='a JSON object mapping each event to its slot, e.g. {"M1": 3, "M2": 1, "M3": 5, "M4": 2, "M5": 4}',
        items=items,
        pieces=pieces,
    )


def parse(ans):
    if isinstance(ans, dict):
        return ans
    s = str(ans)
    m = re.search(r"\{.*\}", s, re.S)
    if m:
        for f in (json.loads, ast.literal_eval):
            try:
                d = f(m.group())
                if isinstance(d, dict):
                    return d
            except (ValueError, SyntaxError):
                pass
    pairs = re.findall(r"\b([A-Za-z]\d+)\b(?:\s*(?:[:=]|->|is|at|in|slot))*\s*\"?(\d+)", s)
    if pairs:
        return {k: int(v) for k, v in pairs}
    return None


def score(item, answer):
    d = parse(answer)
    if not isinstance(d, dict):
        return 0.0
    tag = item["tag"]
    try:
        perm = [int(d.get(f"{tag}{i + 1}", d.get(f"{tag.lower()}{i + 1}", -1))) for i in range(N)]
    except (TypeError, ValueError):
        return 0.0
    if sorted(perm) != list(range(1, N + 1)):
        return 0.0
    return 1.0 if all(holds(tuple(c), perm) for c in item["constraints"]) else 0.0
