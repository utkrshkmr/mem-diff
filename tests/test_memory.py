import random

import pytest

from memdiff.memory import REGISTRY, make, Staged

WORDS = ["Alba", "Bren", "Cato", "works_at", "born_in", "x", "y", "F1", "F2", "L1", "E1", "R1", "T1", "?X", "?Y"]


def rand_args(spec, rng):
    out = {}
    props = spec["function"]["parameters"]["properties"]
    for k, p in props.items():
        if rng.random() < 0.2 and k not in spec["function"]["parameters"]["required"]:
            continue
        if p["type"] == "string":
            out[k] = rng.choice(p.get("enum") or WORDS)
        elif p["type"] == "integer":
            out[k] = rng.choice([-1, 0, 1, 2, 3])
        elif p["type"] == "boolean":
            out[k] = rng.random() < 0.5
        elif p["type"] == "array":
            out[k] = [rng.choice(WORDS) for _ in range(rng.choice([1, 2, 2, 3]))]
    if spec["function"]["name"] == "dl_add_rule":
        out = {"head": "r(?X,?Z)", "body": "works_at(?X,?Y), born_in(?Y,?Z)"}
    return out


def drive(name, acl, seed, staged):
    rng = random.Random(seed)
    m = make(name, acl=acl, schema=None, functional=["works_at"])
    m.agents = ["A", "B"]
    specs = m.tools("solver")
    st = Staged(m, ["A", "B"]) if staged else None
    for rnd in range(6):
        if st:
            st.begin()
        for _ in range(12):
            ag = rng.choice(["A", "B"])
            sp = rng.choice(specs)
            op = sp["function"]["name"]
            args = rand_args(sp, rng)
            if st:
                st.apply(op, args, ag, rnd, "solver")
            else:
                m.apply(op, m.prepare(op, args, ag), ag, rnd, "solver")
        if st:
            st.commit(rnd)
    return m


@pytest.mark.parametrize("name", sorted(REGISTRY))
@pytest.mark.parametrize("acl", ["open", "owner"])
@pytest.mark.parametrize("staged", [False, True])
def test_replay_and_repeatability(name, acl, staged):
    for seed in range(15):
        a = drive(name, acl, seed, staged)
        b = drive(name, acl, seed, staged)
        assert a.state_hash() == b.state_hash()
        assert a.replay_check()


def test_owner_acl_blocks_cross_mutation():
    m = make("kv", acl="owner")
    m.apply("kv_put", {"key": "k", "value": "v"}, "A", 0)
    r = m.apply("kv_delete", {"key": "k"}, "B", 0)
    assert not r["ok"]
    m2 = make("kv", acl="open")
    m2.apply("kv_put", {"key": "k", "value": "v"}, "A", 0)
    r2 = m2.apply("kv_delete", {"key": "k"}, "B", 0)
    assert r2["ok"] and m2.log[-1]["cross"]


def test_kg_functional_conflict_and_corroboration():
    m = make("kg", schema=["works_at"], functional=["works_at"])
    m.apply("kg_add", {"subject": "Ana", "relation": "works_at", "object": "Zed"}, "A", 0)
    r = m.apply("kg_add", {"subject": "ana", "relation": "works_at", "object": "zed"}, "B", 0)
    assert r["corroborated"]
    m.apply("kg_add", {"subject": "Ana", "relation": "works_at", "object": "Qux"}, "B", 0)
    q = m.apply("kg_query", {"subject": "Ana"}, "A", 0)
    assert all("conflicts_with" in f for f in q["facts"])
    assert not m.apply("kg_add", {"subject": "a", "relation": "likes", "object": "b"}, "A", 0)["ok"]


def test_datalog_rules():
    m = make("datalog")
    m.apply("dl_assert", {"predicate": "works_at", "args": ["Ana", "Zed"]}, "A", 0)
    m.apply("dl_assert", {"predicate": "hq_in", "args": ["Zed", "Pom"]}, "B", 0)
    m.apply("dl_add_rule", {"head": "city_of(?P,?C)", "body": "works_at(?P,?X), hq_in(?X,?C)"}, "A", 0)
    r = m.apply("dl_query", {"predicate": "city_of", "args": ["Ana", "?C"]}, "B", 0)
    assert r["results"][0]["fact"] == "city_of(Ana, Pom)" and r["results"][0]["derived"]


def test_crdt_concurrent_add_wins():
    m = make("crdt")
    m.agents = ["A", "B"]
    m.apply("crdt_add", {"item": "x"}, "A", 0)
    st = Staged(m, ["A", "B"])
    st.begin()
    st.apply("crdt_remove", {"item": "x"}, "B", 1, "solver")
    st.apply("crdt_add", {"item": "x"}, "A", 1, "solver")
    st.commit(1)
    assert [i["item"] for i in m.apply("crdt_read", {}, "A", 2)["items"]] == ["x"]


def test_eventlog_immutable_view():
    m = make("eventlog")
    m.apply("log_append", {"type": "claim", "text": "Ana works at Zed"}, "A", 0)
    m.apply("log_append", {"type": "challenge", "text": "false", "ref": 1}, "B", 0)
    v = m.apply("log_view", {}, "A", 0)["claims"][0]
    assert v["status"] == "contested"
    assert m.log[-2]["cross"]


def test_critic_role_restricted():
    m = make("blackboard")
    r = m.apply("bb_post", {"section": "facts", "content": "x"}, "B", 0, role="critic")
    assert not r["ok"]
    assert m.apply("critique", {"target": "E1", "text": "bad"}, "B", 0, role="critic")["ok"]
