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


def claim_pairs(inst, text):
    ents = inst.truth["entities"]
    found = []
    for typ, names in ents.items():
        for n in names:
            for m in re.finditer(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text, re.IGNORECASE):
                found.append((m.start(), n, typ))
    found.sort()
    world = {(s, o) for s, r, o in inst.truth["triples"]}
    true_n = false_n = 0
    for (i, a, ta), (j, b, tb) in zip(found, found[1:]):
        if (ta, tb) in TYPE_PAIRS or (tb, ta) in TYPE_PAIRS:
            if j - i > 160:
                continue
            if (a, b) in world or (b, a) in world:
                true_n += 1
            else:
                false_n += 1
    return true_n, false_n
