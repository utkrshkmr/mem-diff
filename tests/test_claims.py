from memdiff.tasks import generate
from memdiff.tasks.factfind import Tracker


def world():
    inst = generate("factfind", 0)
    tr = Tracker(inst, {})
    T = inst.truth["triples"]
    E = inst.truth["entities"]
    get = lambda r: next(x for x in T if x[1] == r)
    return inst, tr, T, E, get


def test_single_hop_forms_true():
    inst, tr, T, E, get = world()
    f = get("founded_by")
    m = get("managed_by")
    w = get("works_at")
    h = get("headquartered_in")
    for text in [f"{f[0]} was founded by {f[2]}.", f"{f[2]} founded {f[0]}.", f"The founder of {f[0]} is {f[2]}.", f"{f[0]} | founded_by | {f[2]}", f"{f[0]} founded_by {f[2]}"]:
        assert tr.classify(text) == (1, 0), text
        assert (f[0], "founded_by", f[2]) in tr.claims(text)[0]
    for text in [f"{m[0]} is managed by {m[2]}.", f"{m[2]} manages {m[0]}.", f"The manager of {m[0]} is {m[2]}.", f"{m[2]} is the manager of {m[0]}.", f"{m[0]} managed {m[2]}"]:
        assert (m[0], "managed_by", m[2]) in tr.claims(text)[0], text
    assert tr.classify(f"The company where {w[0]} works is {w[2]}.") == (1, 0)
    assert tr.classify(f"{h[0]} is headquartered in {h[2]}.") == (1, 0)


def test_false_and_wrong_type_and_negation():
    inst, tr, T, E, get = world()
    w = get("works_at")
    other_co = next(c for c in E["company"] if c != w[2])
    other_person = next(p for p in E["person"] if p != w[0] and frozenset((w[0], p)) not in tr.reach)
    assert tr.classify(f"{w[0]} works at {other_co}.") == (0, 1)
    assert tr.classify(f"{w[0]} works at {other_person}.") == (0, 1)
    assert tr.classify(f"{w[0]} does not work at {other_co}.") == (0, 0)
    assert tr.classify(f"It is false that {w[0]} works at {other_co}.") == (0, 0)


def test_multihop_skipped_and_derived_not_false():
    inst, tr, T, E, get = world()
    m = get("managed_by")
    city = next(c for c in E["city"])
    assert tr.classify(f"The manager of {m[0]} was born in {city}.") == (0, 0)
    h = get("headquartered_in")
    loc = next(x for x in T if x[1] == "located_in" and x[0] == h[2])
    assert tr.classify(f"{h[0]} is located in {loc[2]}.") == (0, 0)
    assert tr.classify(f"{m[0]} {m[2]}") == (0, 0)
