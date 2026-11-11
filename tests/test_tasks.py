import itertools

from memdiff.tasks import generate, score, allocate, puzzle
from memdiff.tasks.codebank import BANK
from memdiff.tasks.codefix import run_cases, cases_for


def test_factfind_allocation_and_score():
    for seed in range(20):
        inst = generate("factfind", seed)
        assert len(inst.items) == 6
        al = allocate(inst, "collab")
        texts = {"A": {p["id"] for p in al["A"]}, "B": {p["id"] for p in al["B"]}}
        for it in inst.items:
            need = [p for p in inst.pieces if any(i == it["id"] for i, _ in p["items"])]
            if it["kind"] == "self_A":
                assert all(p["id"] in texts["A"] for p in need)
            if it["kind"] == "split":
                assert any(p["id"] not in texts["A"] for p in need)
                assert any(p["id"] not in texts["B"] for p in need)
            assert score(inst, it, it["answer"].upper() + ".") == 1.0
        assert generate("factfind", seed).items == inst.items


def test_puzzle_unique_and_scored():
    for seed in range(10):
        inst = generate("puzzle", seed)
        for it in inst.items:
            sols = [p for p in itertools.permutations(range(1, 6)) if all(puzzle.holds(tuple(c), p) for c in it["constraints"])]
            assert len(sols) == 1
            assert score(inst, it, it["answer"]) == 1.0
            assert score(inst, it, "nonsense") == 0.0


def test_codebank_ref_passes_buggy_fails():
    for t in BANK:
        vis, hid = cases_for(t)
        assert run_cases(t["ref"], t["fn"], vis + hid) == 1.0
        assert run_cases(t["buggy"], t["fn"], vis + hid) < 1.0
        assert run_cases("def x(:", t["fn"], vis) == 0.0


def test_code_score_extracts_fence():
    inst = generate("codefix", 0)
    it = inst.items[0]
    assert score(inst, it, "```python\n" + it["answer"] + "```") == 1.0
