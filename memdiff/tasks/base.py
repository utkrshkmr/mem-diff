import random
from dataclasses import dataclass, field

KINDS = ("split", "self_A", "self_B")


@dataclass
class Instance:
    task: str
    seed: int
    intro: str
    answer_format: str
    items: list
    pieces: list
    schema: list = None
    functional: list = None
    truth: dict = field(default_factory=dict)


def who(kind, hop):
    if kind == "self_A":
        return {"A"}
    if kind == "self_B":
        return {"B"}
    return {"A"} if hop % 2 == 0 else {"B"}


def allocate(inst, mode):
    rng = random.Random(inst.seed * 7919 + 13)
    kind = {it["id"]: it["kind"] for it in inst.items}
    out = {"A": [], "B": []}
    for p in inst.pieces:
        if mode in ("solo", "critic"):
            ags = {"A", "B"} if mode == "critic" else {"A"}
        elif p["items"]:
            ags = set()
            for iid, hop in p["items"]:
                ags |= who(kind[iid], hop)
        else:
            ags = {rng.choice("AB")}
        for a in sorted(ags):
            out[a].append(p)
    return out


def assign_kinds(items):
    for i, it in enumerate(items):
        it["kind"] = KINDS[i % 3]
    return items


def norm_answer(s):
    return " ".join(str(s).strip().strip(".").casefold().split())
