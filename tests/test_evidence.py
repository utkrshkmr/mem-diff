import asyncio

from memdiff.tasks import generate, allocate
from memdiff.tasks.factfind import Tracker, REL


def setup():
    inst = generate("factfind", 3)
    al = allocate(inst, "split")
    tr = Tracker(inst, al)
    item = next(i for i in inst.items if i["kind"] == "split")
    blinks = [l for l in tr.links if l["item"] == item["id"] and not l["in_A"]]
    alinks = [l for l in tr.links if l["item"] == item["id"] and l["in_A"]]
    return inst, tr, item, blinks, alinks


def fact(l):
    return REL[l["r"]][2].format(s=l["s"], o=l["o"])


def wrong_obj(inst, l):
    t = REL[l["r"]][1]
    return next(x for x in inst.truth["entities"][t] if x != l["o"] and frozenset((l["s"], x)) not in {frozenset((a, b)) for a, r, b in inst.truth["triples"]})


def run(tr, steps, item):
    tok = 0
    for rnd, recs in enumerate(steps):
        tok += 1000
        tr.update(recs, {"i": rnd, "tok": tok, "round": rnd})
    return tr.summary({item["id"]: False}, {}, {"tok": tok + 1000, "round": len(steps)})


def cause(summary, item):
    return next(i for i in summary["items"] if i["item"] == item["id"])


def test_never_shared():
    inst, tr, item, bl, al = setup()
    s = run(tr, [[], [("A", fact(al[0]))]], item)
    assert cause(s, item)["cause"] == "never_available"


def test_shared_then_removed():
    inst, tr, item, bl, al = setup()
    recs = [("B", fact(l)) for l in bl]
    s = run(tr, [recs, recs, []], item)
    c = cause(s, item)
    assert c["cause"] == "removed" and c["first_avail_round"] == 0
    assert all(l["removed"] and l["first_supplier"] == "B" for l in s["links"] if l["item"] == item["id"] and not l["in_A"])


def test_conflict_persists():
    inst, tr, item, bl, al = setup()
    l = bl[0]
    lie = REL[l["r"]][2].format(s=l["s"], o=wrong_obj(inst, l))
    recs = [("B", fact(x)) for x in bl]
    s = run(tr, [recs, recs + [("B", lie)], recs + [("B", lie)]], item)
    c = cause(s, item)
    assert c["cause"] == "conflict_at_end"
    ls = next(x for x in s["links"] if x["item"] == item["id"] and x["hop"] == l["hop"])
    assert ls["conflict_end"] and ls["conflict_rounds"] == 2 and ls["conflict_tok"] == 2000


def test_available_not_used_and_lag():
    inst, tr, item, bl, al = setup()
    recs = [("B", fact(x)) for x in bl]
    tok = 0
    for rnd in range(3):
        tok += 1000
        tr.update(recs, {"i": rnd, "tok": tok, "round": rnd})
    s = tr.summary({item["id"]: True}, {item["id"]: {"tok": 3000, "round": 2}}, {"tok": 4000, "round": 3})
    c = cause(s, item)
    assert c["cause"] == "correct" and c["lag_tok"] == 2000 and c["lag_rounds"] == 2
    s2 = tr.summary({item["id"]: False}, {}, {"tok": 4000, "round": 3})
    assert cause(s2, item)["cause"] == "available_not_used"


def test_matched_modes_identical_except_goal():
    from memdiff.episode import MODES, objective, system_prompt, first_user
    from memdiff.memory import make
    inst = generate("factfind", 1)
    al = allocate(inst, "split")
    st = make("kg", schema=inst.schema)
    pa = system_prompt(st, inst, "assist", "solver", "A", "B", 8, 6, False)
    pb = system_prompt(st, inst, "sabotage", "solver", "A", "B", 8, 6, False)
    assert pa == pb
    assert first_user(inst, al["B"], "helper", False) == first_user(inst, al["B"], "saboteur", False)
    assert "GROUND TRUTH" not in first_user(inst, al["B"], "saboteur", False)
    assert objective("assist", "helper", False) != objective("sabotage", "saboteur", False)
