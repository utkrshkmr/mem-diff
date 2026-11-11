import random
import re

from .base import Instance, assign_kinds, norm_answer

SYL = ["ka", "ve", "lo", "dra", "mi", "tor", "sa", "qen", "bu", "zil", "or", "pha", "nek", "ru", "ty", "mor", "ae", "gis", "len", "vo", "cha", "dun", "ipe", "wex"]

REL = {
    "works_at": ("person", "company", "{s} works at {o}."),
    "managed_by": ("person", "person", "{s} is managed by {o}."),
    "born_in": ("person", "city", "{s} was born in {o}."),
    "headquartered_in": ("company", "city", "{s} is headquartered in {o}."),
    "located_in": ("city", "country", "{s} is located in {o}."),
    "founded_by": ("company", "person", "{s} was founded by {o}."),
}

TEMPLATES = [
    ("In which country is the headquarters of the company where {x} works?", "person", ["works_at", "headquartered_in", "located_in"]),
    ("In which city was the manager of {x} born?", "person", ["managed_by", "born_in"]),
    ("Who founded the company where {x} works?", "person", ["works_at", "founded_by"]),
    ("In which country was the founder of {x} born?", "company", ["founded_by", "born_in", "located_in"]),
    ("In which country was {x} born?", "person", ["born_in", "located_in"]),
    ("Where is the company that employs the manager of {x} headquartered (city)?", "person", ["managed_by", "works_at", "headquartered_in"]),
]


def name(rng, used, n=2, suffix=""):
    while True:
        w = "".join(rng.choice(SYL) for _ in range(n)).capitalize()
        if suffix:
            w += suffix
        if w not in used:
            used.add(w)
            return w


def generate(seed, n_items=6):
    rng = random.Random(seed)
    used = set()
    ents = {
        "person": [f"{name(rng, used)} {name(rng, used, 3)}" for _ in range(16)],
        "company": [name(rng, used, 2, " Corp") for _ in range(9)],
        "city": [name(rng, used, 3) for _ in range(10)],
        "country": [name(rng, used, 2, "ia") for _ in range(6)],
    }
    facts = {}
    for p in ents["person"]:
        facts[(p, "works_at")] = rng.choice(ents["company"])
        facts[(p, "born_in")] = rng.choice(ents["city"])
        facts[(p, "managed_by")] = rng.choice([q for q in ents["person"] if q != p])
    for c in ents["company"]:
        facts[(c, "headquartered_in")] = rng.choice(ents["city"])
        facts[(c, "founded_by")] = rng.choice(ents["person"])
    for c in ents["city"]:
        facts[(c, "located_in")] = rng.choice(ents["country"])

    def chain(x, rels):
        cur, used_f = x, []
        for r in rels:
            used_f.append((cur, r))
            cur = facts[(cur, r)]
        return cur, used_f

    cands = []
    for t, (q, typ, rels) in enumerate(TEMPLATES):
        for x in ents[typ]:
            cands.append((t, x))
    rng.shuffle(cands)
    items, seen_t, seen_x, taken = [], {}, set(), set()
    for t, x in cands:
        if seen_t.get(t, 0) >= 2 or x in seen_x:
            continue
        q, typ, rels = TEMPLATES[t]
        ans, used_f = chain(x, rels)
        if taken & set(used_f):
            continue
        taken |= set(used_f)
        items.append({"id": f"q{len(items) + 1}", "prompt": q.format(x=x), "answer": ans, "chain": used_f})
        seen_t[t] = seen_t.get(t, 0) + 1
        seen_x.add(x)
        if len(items) == n_items:
            break
    assign_kinds(items)
    uses = {}
    for it in items:
        for hop, f in enumerate(it["chain"]):
            uses.setdefault(f, []).append((it["id"], hop))
    pieces = []
    relevant = list(uses)
    others = [f for f in facts if f not in uses]
    rng.shuffle(others)
    for f in relevant + others[: len(relevant)]:
        s, r = f
        pieces.append({"id": f"p{len(pieces) + 1}", "text": REL[r][2].format(s=s, o=facts[f]), "items": uses.get(f, []), "triple": [s, r, facts[f]]})
    rng.shuffle(pieces)
    for it in items:
        it["chain"] = [list(f) for f in it["chain"]]
    triples = [[s, r, o] for (s, r), o in sorted(facts.items())]
    return Instance(
        task="factfind",
        seed=seed,
        intro="Answer multi-hop questions about a fictional world. Each fact you know is in your private evidence; facts held only by the other agent can reach you only through shared memory. Answers are exact entity names.",
        answer_format="the exact entity name, e.g. 'Kavelo Dratorsa' or 'Mizilia'",
        items=items,
        pieces=pieces,
        schema=list(REL),
        functional=list(REL),
        truth={"triples": triples, "entities": ents},
    )


def score(item, answer):
    return 1.0 if norm_answer(answer) == norm_answer(item["answer"]) else 0.0


TYPE_PAIRS = {}
for r, (a, b, _) in REL.items():
    TYPE_PAIRS.setdefault((a, b), set()).add(r)

KW = {
    "works_at": ("works at", "works for", "work at", "employ", "works_at", "works in", "job at"),
    "managed_by": ("managed by", "manager", "manages", "managed_by", "reports to", "boss", "supervis"),
    "born_in": ("born in", "born_in", "birthplace", "was born"),
    "headquartered_in": ("headquarter", "hq", "based in", "headquartered_in"),
    "located_in": ("located in", "located_in", "is in", "lies in", "in the country", "country"),
    "founded_by": ("founded by", "founder", "founded", "founded_by", "established by", "created by"),
}


def rel_between(ta, tb, gap):
    g = " " + gap.casefold().replace("_", " ") + " "
    for r, (x, y, _) in REL.items():
        if {x, y} == {ta, tb} or (x == ta and y == tb) or (x == tb and y == ta):
            if any(k.replace("_", " ") in g for k in KW[r]):
                return r
    return None


class Tracker:
    def __init__(self, inst, alloc):
        ents = inst.truth["entities"]
        self.typ = {n.casefold(): (n, t) for t, ns in ents.items() for n in ns}
        names = sorted(self.typ, key=len, reverse=True)
        self.rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in names) + r")(?!\w)", re.IGNORECASE)
        self.world = {frozenset((a, b)) for a, r, b in inst.truth["triples"]}
        self.cache = {}
        obj = {(a, r): b for a, r, b in inst.truth["triples"]}
        priv = {a: {tuple(p["triple"]) for p in alloc.get(a, []) if p.get("triple")} for a in ("A", "B")}
        self.links = []
        for it in inst.items:
            for hop, (a, r) in enumerate(it["chain"]):
                b = obj[(a, r)]
                self.links.append({"item": it["id"], "hop": hop, "s": a, "r": r, "o": b, "kind": it["kind"], "in_A": (a, r, b) in priv["A"], "in_B": (a, r, b) in priv["B"], "hist": [], "_last": None})
        self.items = [it["id"] for it in inst.items]
        self.item_hist = {i: [] for i in self.items}
        self.cur = {}

    def pairs(self, text):
        if text not in self.cache:
            found = [(m.start(), m.end(), self.typ[m.group(1).casefold()]) for m in self.rx.finditer(text)]
            out = []
            for (i, ie, (a, ta)), (j, je, (b, tb)) in zip(found, found[1:]):
                if a == b or j - ie > 80 or not ((ta, tb) in TYPE_PAIRS or (tb, ta) in TYPE_PAIRS):
                    continue
                if re.search(r"\b(of|where|whose|that employs)\s+(the\s+)?$", text[max(0, i - 30):i], re.IGNORECASE):
                    continue
                if rel_between(ta, tb, text[ie:j]):
                    out.append((a, b))
            self.cache[text] = out
        return self.cache[text]

    def classify(self, text):
        t = f = 0
        for a, b in self.pairs(text):
            if frozenset((a, b)) in self.world:
                t += 1
            else:
                f += 1
        return t, f

    def update(self, records, at):
        index = {}
        partner = {}
        for au, tx in records:
            for a, b in self.pairs(tx):
                index.setdefault(frozenset((a, b)), set()).add(au)
                partner.setdefault(a, set()).add(b)
                partner.setdefault(b, set()).add(a)
        per_item = {i: {"avail": True, "mem": True} for i in self.items}
        lp = lc = la = 0
        for L in self.links:
            k = frozenset((L["s"], L["o"]))
            present = k in index
            by = sorted(index.get(k, ()))
            ot = self.typ[L["o"].casefold()][1]
            conf = sorted(x for x in partner.get(L["s"], ()) if x != L["o"] and self.typ[x.casefold()][1] == ot and frozenset((L["s"], x)) not in self.world)
            st = (present, tuple(by), tuple(conf))
            if st != L["_last"]:
                L["hist"].append({**at, "present": present, "by": by, "conflict": conf})
                L["_last"] = st
            avail = L["in_A"] or present
            lp += present
            lc += bool(conf)
            la += avail
            per_item[L["item"]]["avail"] &= avail
            per_item[L["item"]]["mem"] &= present
        for i, v in per_item.items():
            if self.cur.get(i) != v["avail"]:
                self.item_hist[i].append({**at, "avail": v["avail"]})
                self.cur[i] = v["avail"]
        return {
            "ev_items_avail_A": sum(v["avail"] for v in per_item.values()),
            "ev_items_in_mem": sum(v["mem"] for v in per_item.values()),
            "ev_links_present": lp,
            "ev_links_conflict": lc,
            "ev_links_avail_A": la,
            "ev_links_total": len(self.links),
        }

    def summary(self, final_correct, first_correct, end):
        links = []
        for L in self.links:
            h = L["hist"]
            fp = next((e for e in h if e["present"]), None)
            last = h[-1] if h else {"present": False, "conflict": [], "by": []}
            ctok = cr = 0.0
            for e, nx in zip(h, h[1:] + [end]):
                if e["conflict"]:
                    ctok += nx["tok"] - e["tok"]
                    cr += nx["round"] - e["round"]
            sup = sorted({a for e in h for a in e["by"]})
            links.append({
                "item": L["item"], "hop": L["hop"], "kind": L["kind"], "rel": L["r"], "in_A": L["in_A"], "in_B": L["in_B"],
                "first_present_tok": fp["tok"] if fp else None, "first_present_round": fp["round"] if fp else None, "first_supplier": fp["by"][0] if fp and fp["by"] else None,
                "suppliers": sup, "present_end": bool(last["present"]), "removed": bool(fp) and not last["present"],
                "ever_conflict": any(e["conflict"] for e in h), "conflict_end": bool(last["conflict"]), "conflict_tok": ctok, "conflict_rounds": cr,
                "n_changes": len(h),
            })
        items = []
        for i in self.items:
            ih = self.item_hist[i]
            fa = next((e for e in ih if e["avail"]), None)
            ever = fa is not None
            at_end = bool(ih and ih[-1]["avail"])
            cend = any(l["conflict_end"] for l in links if l["item"] == i)
            ok = bool(final_correct.get(i))
            if ok:
                cause = "correct"
            elif not ever:
                cause = "never_available"
            elif not at_end:
                cause = "removed"
            elif cend:
                cause = "conflict_at_end"
            else:
                cause = "available_not_used"
            fc = first_correct.get(i)
            items.append({
                "item": i, "kind": next(l["kind"] for l in links if l["item"] == i), "correct_end": ok, "cause": cause,
                "first_avail_tok": fa["tok"] if fa else None, "first_avail_round": fa["round"] if fa else None,
                "first_correct_tok": fc["tok"] if fc else None, "first_correct_round": fc["round"] if fc else None,
                "lag_tok": (fc["tok"] - fa["tok"]) if fc and fa else None, "lag_rounds": (fc["round"] - fa["round"]) if fc and fa else None,
                "avail_at_end": at_end, "conflict_at_end": cend,
            })
        return {"links": links, "items": items}


def claim_pairs(inst, text):
    tr = inst.truth.get("_tracker")
    if tr is None:
        tr = Tracker(inst, {})
        inst.truth["_tracker"] = tr
    return tr.classify(text)
