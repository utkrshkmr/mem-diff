import re

from .base import Memory, Reject, P, tool, norm


class Scratchpad(Memory):
    name = "scratchpad"
    description = "a shared free-text document made of numbered lines; anyone can append, edit or delete lines; authorship is not shown"
    reads = ("read_doc",)
    writes = ("append_line", "edit_line", "delete_line")
    destructive = ("edit_line", "delete_line")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.lines = []

    def specs(self):
        return [
            tool("read_doc", "Read the whole shared document."),
            tool("append_line", "Append a line of text.", {"text": P("string", "line text")}, ["text"]),
            tool("edit_line", "Replace the text of a line.", {"line_id": P("string", "line id, e.g. L3"), "text": P("string", "new text")}, ["line_id", "text"]),
            tool("delete_line", "Delete a line.", {"line_id": P("string", "line id")}, ["line_id"]),
        ]

    def _find(self, line_id):
        for i, l in enumerate(self.lines):
            if l["id"] == str(line_id).strip():
                return i, l
        raise KeyError(f"no line {line_id}")

    def op_read_doc(self, agent, rnd):
        return {"lines": [f"{l['id']}: {l['text']}" for l in self.lines]}

    def op_append_line(self, agent, rnd, text):
        lid = self.next_id("L")
        self.lines.append({"id": lid, "text": self.clip(text), "author": agent})
        return {"line_id": lid}

    def op_edit_line(self, agent, rnd, line_id, text):
        i, l = self._find(line_id)
        self.own(l["author"], agent)
        self.touch_other(l["author"], agent)
        l["text"] = self.clip(text)
        l["author"] = agent if self.acl == "open" else l["author"]
        l["edits"] = l.get("edits", 0) + 1
        return {}

    def op_delete_line(self, agent, rnd, line_id):
        i, l = self._find(line_id)
        self.own(l["author"], agent)
        self.touch_other(l["author"], agent)
        self.lines.pop(i)
        return {}

    def op_critique(self, agent, rnd, target, text):
        return self.op_append_line(agent, rnd, f"[CRITIQUE {target}] {text}")

    def state(self):
        return {"seq": self.seq, "lines": self.lines}

    def extra(self):
        c = sum(1 for l in self.lines if l["text"].startswith("[CRITIQUE"))
        return {"lines": len(self.lines), "lines_edited": sum(1 for l in self.lines if l.get("edits")), "contested": c}

    def records(self):
        return [(l["author"], l["text"]) for l in self.lines]


class KV(Memory):
    name = "kv"
    description = "a shared key-value store with versions; writes and deletes take an expected_version (compare-and-swap): -1 = any, 0 = key must not exist"
    reads = ("kv_get", "kv_list")
    writes = ("kv_put", "kv_delete")
    destructive = ("kv_delete",)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.data = {}

    def specs(self):
        return [
            tool("kv_get", "Get a key.", {"key": P("string", "key")}, ["key"]),
            tool("kv_list", "List keys (with value, version, author) under a prefix.", {"prefix": P("string", "key prefix, empty for all")}),
            tool("kv_put", "Write a key.", {"key": P("string", "key"), "value": P("string", "value"), "expected_version": P("integer", "-1 any, 0 must not exist, n current version")}, ["key", "value"]),
            tool("kv_delete", "Delete a key.", {"key": P("string", "key"), "expected_version": P("integer", "-1 any or current version")}, ["key"]),
        ]

    def _cas(self, key, ev):
        cur = self.data.get(key)
        v = cur["version"] if cur else 0
        if ev is None or int(ev) == -1:
            return cur
        if int(ev) != v:
            raise Reject(f"version conflict on {key}: current version {v}")
        return cur

    def op_kv_get(self, agent, rnd, key):
        e = self.data.get(str(key))
        return {"found": False} if e is None else {"found": True, **e}

    def op_kv_list(self, agent, rnd, prefix=""):
        ks = sorted(k for k in self.data if k.startswith(str(prefix or "")))
        return {"items": [{"key": k, **self.data[k]} for k in ks[:80]], "total": len(ks)}

    def op_kv_put(self, agent, rnd, key, value, expected_version=-1):
        key = str(key)[:120]
        cur = self._cas(key, expected_version)
        if cur:
            self.own(cur["author"], agent)
            self.touch_other(cur["author"], agent)
        v = (cur["version"] if cur else 0) + 1
        self.data[key] = {"value": self.clip(value), "version": v, "author": agent}
        self.seq += 1
        return {"version": v}

    def op_kv_delete(self, agent, rnd, key, expected_version=-1):
        key = str(key)
        cur = self._cas(key, expected_version)
        if not cur:
            raise KeyError(f"no key {key}")
        self.own(cur["author"], agent)
        self.touch_other(cur["author"], agent)
        del self.data[key]
        self.seq += 1
        return {}

    def op_critique(self, agent, rnd, target, text):
        return self.op_kv_put(agent, rnd, f"critique/{self.seq + 1}", f"{target}: {text}", 0)

    def state(self):
        return {"seq": self.seq, "data": self.data}

    def extra(self):
        vs = [e["version"] for e in self.data.values()]
        c = sum(1 for k in self.data if k.startswith("critique/"))
        return {"keys": len(self.data), "mean_version": sum(vs) / len(vs) if vs else 0.0, "overwritten_keys": sum(v > 1 for v in vs), "contested": c}

    def records(self):
        return [(e["author"], f"{k} = {e['value']}") for k, e in sorted(self.data.items())]


class Blackboard(Memory):
    name = "blackboard"
    description = "a shared blackboard with sections (facts, hypotheses, plans, answers, critiques); entries have an author, a status (proposed/accepted/rejected) and endorsements"
    reads = ("bb_read",)
    writes = ("bb_post", "bb_set_status", "bb_endorse", "bb_edit", "bb_remove")
    destructive = ("bb_set_status", "bb_edit", "bb_remove")
    sections = ("facts", "hypotheses", "plans", "answers", "critiques")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.entries = {}
        self.order = []

    def specs(self):
        sec = P("string", "section", enum=list(self.sections))
        return [
            tool("bb_read", "Read entries, optionally filtered.", {"section": sec, "status": P("string", "status filter", enum=["proposed", "accepted", "rejected"])}),
            tool("bb_post", "Post an entry.", {"section": sec, "content": P("string", "content")}, ["section", "content"]),
            tool("bb_set_status", "Set an entry's status with a reason.", {"entry_id": P("string", "entry id"), "status": P("string", "new status", enum=["proposed", "accepted", "rejected"]), "reason": P("string", "reason")}, ["entry_id", "status"]),
            tool("bb_endorse", "Endorse an entry.", {"entry_id": P("string", "entry id")}, ["entry_id"]),
            tool("bb_edit", "Edit an entry's content.", {"entry_id": P("string", "entry id"), "content": P("string", "new content")}, ["entry_id", "content"]),
            tool("bb_remove", "Remove an entry.", {"entry_id": P("string", "entry id")}, ["entry_id"]),
        ]

    def _get(self, eid):
        e = self.entries.get(str(eid).strip())
        if e is None:
            raise KeyError(f"no entry {eid}")
        return e

    def op_bb_read(self, agent, rnd, section=None, status=None):
        out = []
        for i in self.order:
            e = self.entries[i]
            if section and e["section"] != section:
                continue
            if status and e["status"] != status:
                continue
            out.append({"id": i, **e})
        return {"entries": out[-80:], "total": len(out)}

    def op_bb_post(self, agent, rnd, section, content, ref=None):
        if section not in self.sections:
            raise ValueError(f"section must be one of {self.sections}")
        i = self.next_id("E")
        self.entries[i] = {"section": section, "content": self.clip(content), "author": agent, "status": "proposed", "endorsed_by": [], "status_log": [], "ref": ref}
        self.order.append(i)
        return {"entry_id": i}

    def op_bb_set_status(self, agent, rnd, entry_id, status, reason=""):
        e = self._get(entry_id)
        if status not in ("proposed", "accepted", "rejected"):
            raise ValueError("bad status")
        self.touch_other(e["author"], agent)
        e["status"] = status
        e["status_log"].append({"by": agent, "status": status, "reason": self.clip(reason)})
        return {}

    def op_bb_endorse(self, agent, rnd, entry_id):
        e = self._get(entry_id)
        if agent not in e["endorsed_by"]:
            e["endorsed_by"].append(agent)
        return {}

    def op_bb_edit(self, agent, rnd, entry_id, content):
        e = self._get(entry_id)
        self.own(e["author"], agent)
        self.touch_other(e["author"], agent)
        e["content"] = self.clip(content)
        return {}

    def op_bb_remove(self, agent, rnd, entry_id):
        e = self._get(entry_id)
        self.own(e["author"], agent)
        self.touch_other(e["author"], agent)
        i = str(entry_id).strip()
        del self.entries[i]
        self.order.remove(i)
        return {}

    def op_critique(self, agent, rnd, target, text):
        return self.op_bb_post(agent, rnd, "critiques", f"on {target}: {text}", ref=str(target))

    def state(self):
        return {"seq": self.seq, "entries": self.entries, "order": self.order}

    def extra(self):
        st = [e["status"] for e in self.entries.values()]
        return {
            "entries": len(st),
            "accepted": st.count("accepted"),
            "rejected": st.count("rejected"),
            "proposed": st.count("proposed"),
            "endorsed": sum(1 for e in self.entries.values() if e["endorsed_by"]),
            "status_flips": sum(len(e["status_log"]) for e in self.entries.values()),
            "contested": st.count("rejected") + sum(1 for e in self.entries.values() if e["section"] == "critiques"),
        }

    def records(self):
        return [(self.entries[i]["author"], self.entries[i]["content"]) for i in self.order]


class TemporalKG(Memory):
    name = "kg"
    description = "a shared temporal knowledge graph of (subject, relation, object) facts; facts are never deleted, only retracted (closed in time); repeated assertions by different agents are recorded as corroboration; disputes are attached to facts"
    reads = ("kg_query", "kg_neighbors")
    writes = ("kg_add", "kg_retract")
    destructive = ("kg_retract",)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.edges = {}
        self.notes = []

    def specs(self):
        rel = P("string", "relation" + (f"; one of {self.schema}" if self.schema else ""))
        return [
            tool("kg_query", "Query facts by exact match on any of subject, relation, object (omit to wildcard).", {"subject": P("string", "subject"), "relation": rel, "object": P("string", "object"), "include_retracted": P("boolean", "include retracted facts")}),
            tool("kg_neighbors", "All current facts touching an entity.", {"entity": P("string", "entity")}, ["entity"]),
            tool("kg_add", "Assert a fact.", {"subject": P("string", "subject"), "relation": rel, "object": P("string", "object"), "evidence": P("string", "optional evidence")}, ["subject", "relation", "object"]),
            tool("kg_retract", "Retract a fact (closes its validity; history kept).", {"fact_id": P("string", "fact id"), "reason": P("string", "reason")}, ["fact_id"]),
        ]

    def critique_spec(self):
        return tool("critique", "Dispute a fact (by fact id) or flag an entity.", {"target": P("string", "fact id like F3, or an entity"), "text": P("string", "the dispute")}, ["target", "text"])

    def _view(self, i):
        e = self.edges[i]
        v = {"id": i, "subject": e["s"], "relation": e["r"], "object": e["o"], "asserted_by": e["by"], "retracted": e["to"] is not None, "disputes": e["disputes"]}
        if e["to"] is not None:
            v["retraction"] = e["retraction"]
        if norm(e["r"]) in {norm(x) for x in self.functional} and e["to"] is None:
            others = [j for j, f in self.edges.items() if f["to"] is None and norm(f["s"]) == norm(e["s"]) and norm(f["r"]) == norm(e["r"]) and j != i]
            if others:
                v["conflicts_with"] = others
        return v

    def _match(self, e, s, r, o):
        return (not s or norm(e["s"]) == norm(s)) and (not r or norm(e["r"]) == norm(r)) and (not o or norm(e["o"]) == norm(o))

    def op_kg_query(self, agent, rnd, subject=None, relation=None, object=None, include_retracted=False):
        ids = [i for i, e in self.edges.items() if self._match(e, subject, relation, object) and (include_retracted or e["to"] is None)]
        return {"facts": [self._view(i) for i in ids[:60]], "total": len(ids)}

    def op_kg_neighbors(self, agent, rnd, entity):
        ids = [i for i, e in self.edges.items() if e["to"] is None and (norm(e["s"]) == norm(entity) or norm(e["o"]) == norm(entity))]
        notes = [n for n in self.notes if norm(n["target"]) == norm(entity)]
        return {"facts": [self._view(i) for i in ids[:60]], "flags": notes}

    def op_kg_add(self, agent, rnd, subject, relation, object, evidence=""):
        if self.schema and norm(relation) not in [norm(x) for x in self.schema]:
            raise Reject(f"relation must be one of {self.schema}")
        for i, e in self.edges.items():
            if e["to"] is None and self._match(e, subject, relation, object):
                if agent not in e["by"]:
                    e["by"].append(agent)
                return {"fact_id": i, "corroborated": True}
        i = self.next_id("F")
        self.edges[i] = {"s": self.clip(subject)[:100], "r": self.clip(relation)[:60], "o": self.clip(object)[:100], "by": [agent], "author": agent, "from": rnd, "to": None, "retraction": None, "disputes": [], "evidence": self.clip(evidence)}
        return {"fact_id": i}

    def op_kg_retract(self, agent, rnd, fact_id, reason=""):
        e = self.edges.get(str(fact_id).strip())
        if e is None:
            raise KeyError(f"no fact {fact_id}")
        if e["to"] is not None:
            raise Reject("already retracted")
        self.own(e["author"], agent)
        self.touch_other(e["author"], agent)
        e["to"] = rnd
        e["retraction"] = {"by": agent, "reason": self.clip(reason)}
        return {}

    def op_critique(self, agent, rnd, target, text):
        t = str(target).strip()
        if t in self.edges:
            self.edges[t]["disputes"].append({"by": agent, "text": self.clip(text)})
        else:
            self.notes.append({"target": t, "by": agent, "text": self.clip(text)})
        self.seq += 1
        return {}

    def state(self):
        return {"seq": self.seq, "edges": self.edges, "notes": self.notes}

    def extra(self):
        act = {i: e for i, e in self.edges.items() if e["to"] is None}
        nodes = {norm(e["s"]) for e in act.values()} | {norm(e["o"]) for e in act.values()}
        par = {n: n for n in nodes}

        def f(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for e in act.values():
            par[f(norm(e["s"]))] = f(norm(e["o"]))
        groups = {}
        for e in act.values():
            if norm(e["r"]) in {norm(x) for x in self.functional}:
                groups.setdefault((norm(e["s"]), norm(e["r"])), set()).add(norm(e["o"]))
        disputed = sum(1 for e in self.edges.values() if e["disputes"])
        retracted = len(self.edges) - len(act)
        return {
            "edges_active": len(act),
            "edges_retracted": retracted,
            "edges_disputed": disputed,
            "edges_corroborated": sum(1 for e in act.values() if len(e["by"]) > 1),
            "nodes": len(nodes),
            "components": len({f(n) for n in nodes}),
            "functional_conflicts": sum(1 for v in groups.values() if len(v) > 1),
            "flags": len(self.notes),
            "contested": disputed + retracted,
        }

    def records(self):
        out = [(e["author"], f"{e['s']} {e['r']} {e['o']}") for e in self.edges.values() if e["to"] is None]
        out += [(d["by"], d["text"]) for e in self.edges.values() for d in e["disputes"]]
        out += [(n["by"], n["text"]) for n in self.notes]
        return out


class EventLog(Memory):
    name = "eventlog"
    description = "a shared append-only event log; nothing can be edited or deleted; claims can be retracted or challenged only by appending events that reference them; a derived view shows each claim's status"
    reads = ("log_read", "log_view")
    writes = ("log_append",)
    types = ("claim", "retract", "challenge", "plan", "note", "answer_proposal")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.events = []

    def specs(self):
        return [
            tool("log_read", "Read events.", {"since": P("integer", "first seq to return"), "type": P("string", "type filter", enum=list(self.types) + ["critique"]), "author": P("string", "author filter")}),
            tool("log_view", "Derived view: every claim with status active / retracted (by its author) / contested (by others)."),
            tool("log_append", "Append an event.", {"type": P("string", "event type", enum=list(self.types)), "text": P("string", "content"), "ref": P("integer", "seq of referenced event, for retract/challenge")}, ["type", "text"]),
        ]

    def op_log_read(self, agent, rnd, since=0, type=None, author=None):
        ev = [e for e in self.events if e["seq"] >= int(since or 0) and (not type or e["type"] == type) and (not author or e["author"] == author)]
        return {"events": ev[-80:], "total": len(ev)}

    def view(self):
        claims = {e["seq"]: {"seq": e["seq"], "author": e["author"], "text": e["text"], "status": "active", "challenges": []} for e in self.events if e["type"] == "claim"}
        for e in self.events:
            r = e.get("ref")
            if r in claims:
                c = claims[r]
                if e["type"] == "retract" and e["author"] == c["author"]:
                    c["status"] = "retracted"
                elif e["type"] in ("retract", "challenge", "critique"):
                    c["challenges"].append({"by": e["author"], "text": e["text"]})
                    if c["status"] == "active":
                        c["status"] = "contested"
        return list(claims.values())

    def op_log_view(self, agent, rnd):
        v = self.view()
        return {"claims": v[-80:], "total": len(v)}

    def op_log_append(self, agent, rnd, type, text, ref=None):
        if type not in self.types and type != "critique":
            raise ValueError(f"type must be one of {self.types}")
        ref = int(ref) if ref not in (None, "") else None
        if ref is not None:
            if not (1 <= ref <= len(self.events)):
                raise KeyError(f"no event {ref}")
            if self.events[ref - 1]["author"] != agent:
                self._cur["other"] = type in ("retract", "challenge", "critique")
        self.seq += 1
        self.events.append({"seq": self.seq, "round": rnd, "author": agent, "type": type, "text": self.clip(text), "ref": ref})
        return {"seq": self.seq}

    def op_critique(self, agent, rnd, target, text):
        m = re.search(r"\d+", str(target))
        ref = int(m.group()) if m and 1 <= int(m.group()) <= len(self.events) else None
        return self.op_log_append(agent, rnd, "critique", f"[{target}] {text}", ref)

    def state(self):
        return {"seq": self.seq, "events": self.events}

    def extra(self):
        v = self.view()
        st = [c["status"] for c in v]
        return {"events": len(self.events), "claims": len(v), "claims_active": st.count("active"), "claims_retracted": st.count("retracted"), "claims_contested": st.count("contested"), "contested": st.count("contested")}

    def records(self):
        return [(e["author"], e["text"]) for e in self.events if e["type"] != "retract"]


class TupleSpace(Memory):
    name = "tuplespace"
    description = "a Linda-style shared tuple space; tuples are lists of strings; rd reads matching tuples, take removes and returns the first match; '*' is a wildcard"
    reads = ("ts_rd", "ts_count")
    writes = ("ts_out", "ts_take")
    destructive = ("ts_take",)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.tuples = []

    def specs(self):
        arr = lambda d: P("array", d, items={"type": "string"})
        return [
            tool("ts_rd", "Read all tuples matching a pattern.", {"pattern": arr("pattern; '*' matches any field; [] matches all")}),
            tool("ts_count", "Count tuples matching a pattern.", {"pattern": arr("pattern")}),
            tool("ts_out", "Write a tuple.", {"fields": arr("tuple fields")}, ["fields"]),
            tool("ts_take", "Remove and return the first tuple matching a pattern.", {"pattern": arr("pattern")}, ["pattern"]),
        ]

    @staticmethod
    def _m(t, p):
        if not p:
            return True
        if len(t) != len(p):
            return False
        return all(x == "*" or norm(x) == norm(y) for x, y in zip(p, t))

    def _pat(self, pattern):
        if pattern is None:
            return []
        if isinstance(pattern, str):
            pattern = [pattern]
        return [str(x) for x in pattern]

    def op_ts_rd(self, agent, rnd, pattern=None):
        p = self._pat(pattern)
        m = [t for t in self.tuples if self._m(t["fields"], p)]
        return {"tuples": [{"id": t["id"], "fields": t["fields"], "author": t["author"]} for t in m[:80]], "total": len(m)}

    def op_ts_count(self, agent, rnd, pattern=None):
        p = self._pat(pattern)
        return {"count": sum(1 for t in self.tuples if self._m(t["fields"], p))}

    def op_ts_out(self, agent, rnd, fields):
        f = [self.clip(x)[:200] for x in self._pat(fields)][:8]
        if not f:
            raise ValueError("empty tuple")
        i = self.next_id("T")
        self.tuples.append({"id": i, "fields": f, "author": agent})
        return {"tuple_id": i}

    def op_ts_take(self, agent, rnd, pattern):
        p = self._pat(pattern)
        for k, t in enumerate(self.tuples):
            if self._m(t["fields"], p):
                if self.acl == "owner" and t["author"] != agent:
                    continue
                self.touch_other(t["author"], agent)
                self.tuples.pop(k)
                self.seq += 1
                return {"taken": {"id": t["id"], "fields": t["fields"], "author": t["author"]}}
        return {"taken": None}

    def op_critique(self, agent, rnd, target, text):
        return self.op_ts_out(agent, rnd, ["critique", str(target), str(text)])

    def state(self):
        return {"seq": self.seq, "tuples": self.tuples}

    def extra(self):
        return {"tuples": len(self.tuples), "tuple_kinds": len({norm(t["fields"][0]) for t in self.tuples}), "contested": sum(1 for t in self.tuples if norm(t["fields"][0]) == "critique")}

    def records(self):
        return [(t["author"], " ".join(t["fields"])) for t in self.tuples]


ATOM = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(([^()]*)\)\s*")


def parse_atom(s):
    m = ATOM.fullmatch(s)
    if not m:
        raise ValueError(f"bad atom: {s}")
    args = [a.strip().strip('"').strip("'") for a in m.group(2).split(",")] if m.group(2).strip() else []
    return m.group(1), tuple(args)


def parse_body(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [parse_atom(a) for a in out]


def isvar(x):
    return isinstance(x, str) and x.startswith("?")


def unify(pat, fact, env):
    env = dict(env)
    for p, f in zip(pat, fact):
        if isvar(p):
            if p in env and env[p] != f:
                return None
            env[p] = f
        elif norm(p) != norm(f):
            return None
    return env


class Datalog(Memory):
    name = "datalog"
    description = "a shared Datalog fact base; agents assert ground facts pred(a,b,...) and may add rules head :- body; queries return facts derived to fixpoint; variables start with ?"
    reads = ("dl_query", "dl_list")
    writes = ("dl_assert", "dl_retract", "dl_add_rule", "dl_remove_rule")
    destructive = ("dl_retract", "dl_remove_rule")
    max_derived = 4000

    def __init__(self, **kw):
        super().__init__(**kw)
        self.facts = {}
        self.rules = {}
        self._cache = None

    def specs(self):
        arr = P("array", "arguments (strings; ?X for variables in queries)", items={"type": "string"})
        return [
            tool("dl_query", "Query a predicate; returns matching facts (base and derived).", {"predicate": P("string", "predicate name"), "args": arr}, ["predicate"]),
            tool("dl_list", "List base facts and rules with authors.", {"predicate": P("string", "optional predicate filter")}),
            tool("dl_assert", "Assert a ground fact.", {"predicate": P("string", "predicate"), "args": arr}, ["predicate", "args"]),
            tool("dl_retract", "Retract a base fact.", {"predicate": P("string", "predicate"), "args": arr}, ["predicate", "args"]),
            tool("dl_add_rule", "Add a rule, e.g. head='grand(?X,?Z)' body='parent(?X,?Y), parent(?Y,?Z)'.", {"head": P("string", "head atom"), "body": P("string", "comma-separated body atoms")}, ["head", "body"]),
            tool("dl_remove_rule", "Remove a rule.", {"rule_id": P("string", "rule id")}, ["rule_id"]),
        ]

    def _key(self, predicate, args):
        return f"{norm(predicate)}({','.join(norm(a) for a in args)})"

    def derive(self):
        if self._cache is not None:
            return self._cache
        known = {}
        for k, f in self.facts.items():
            known[k] = (f["pred"], tuple(f["args"]))
        rules = [self.rules[r] for r in sorted(self.rules, key=lambda x: int(x[1:]))]
        changed = True
        while changed and len(known) < self.max_derived:
            changed = False
            for r in rules:
                hp, ha = r["head"][0], r["head"][1]
                envs = [{}]
                for bp, ba in r["body"]:
                    nxt = []
                    for env in envs:
                        for pred, args in list(known.values()):
                            if norm(pred) == norm(bp) and len(args) == len(ba):
                                e2 = unify([env.get(a, a) for a in ba], args, env)
                                if e2 is not None:
                                    nxt.append(e2)
                    envs = nxt[: self.max_derived]
                for env in envs:
                    args = tuple(env.get(a, a) for a in ha)
                    if any(isvar(a) for a in args):
                        continue
                    k = self._key(hp, args)
                    if k not in known:
                        known[k] = (hp, args)
                        changed = True
        self._cache = known
        return known

    def op_dl_query(self, agent, rnd, predicate, args=None):
        args = [str(a) for a in (args or [])]
        out = []
        for k in sorted(self.derive()):
            pred, fa = self.derive()[k]
            if norm(pred) != norm(predicate) or (args and len(args) != len(fa)):
                continue
            if args and unify(args, fa, {}) is None:
                continue
            out.append({"fact": f"{pred}({', '.join(fa)})", "derived": k not in self.facts, "author": self.facts[k]["author"] if k in self.facts else None})
        return {"results": out[:80], "total": len(out)}

    def op_dl_list(self, agent, rnd, predicate=None):
        fs = [{"fact": f"{f['pred']}({', '.join(f['args'])})", "author": f["author"]} for k, f in sorted(self.facts.items()) if not predicate or norm(f["pred"]) == norm(predicate)]
        rs = [{"id": i, "rule": r["text"], "author": r["author"]} for i, r in sorted(self.rules.items())]
        return {"facts": fs[:100], "rules": rs, "total_facts": len(fs)}

    def op_dl_assert(self, agent, rnd, predicate, args):
        args = [self.clip(a)[:100] for a in (args if isinstance(args, list) else [args])]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(predicate)):
            raise ValueError("bad predicate name")
        if any(isvar(a) for a in args):
            raise ValueError("facts must be ground")
        k = self._key(predicate, args)
        if k in self.facts:
            return {"exists": True}
        self.facts[k] = {"pred": str(predicate), "args": args, "author": agent}
        self.seq += 1
        self._cache = None
        return {}

    def op_dl_retract(self, agent, rnd, predicate, args):
        k = self._key(predicate, [str(a) for a in args])
        f = self.facts.get(k)
        if f is None:
            raise KeyError(f"no base fact {k}")
        self.own(f["author"], agent)
        self.touch_other(f["author"], agent)
        del self.facts[k]
        self.seq += 1
        self._cache = None
        return {}

    def op_dl_add_rule(self, agent, rnd, head, body):
        h = parse_atom(head)
        b = parse_body(body)
        if not b:
            raise ValueError("empty body")
        bv = {a for _, args in b for a in args if isvar(a)}
        if any(isvar(a) and a not in bv for a in h[1]):
            raise ValueError("unsafe rule: head variable not in body")
        if len(self.rules) >= 20:
            raise Reject("rule limit reached")
        i = self.next_id("R")
        self.rules[i] = {"head": [h[0], list(h[1])], "body": [[p, list(a)] for p, a in b], "author": agent, "text": f"{head.strip()} :- {body.strip()}"}
        self._cache = None
        return {"rule_id": i}

    def op_dl_remove_rule(self, agent, rnd, rule_id):
        r = self.rules.get(str(rule_id).strip())
        if r is None:
            raise KeyError(f"no rule {rule_id}")
        self.own(r["author"], agent)
        self.touch_other(r["author"], agent)
        del self.rules[str(rule_id).strip()]
        self._cache = None
        return {}

    def op_critique(self, agent, rnd, target, text):
        return self.op_dl_assert(agent, rnd, "critique", [str(target), str(text)])

    def state(self):
        return {"seq": self.seq, "facts": self.facts, "rules": self.rules}

    def snapshot(self):
        c = super().snapshot()
        return c

    def extra(self):
        d = self.derive()
        return {"base_facts": len(self.facts), "rules": len(self.rules), "derived_facts": len(d) - len(self.facts), "predicates": len({norm(f["pred"]) for f in self.facts.values()}), "contested": sum(1 for f in self.facts.values() if norm(f["pred"]) == "critique")}

    def records(self):
        return [(f["author"], f"{f['args'][0] if f['args'] else ''} {f['pred']} {' '.join(f['args'][1:])}") for f in self.facts.values()]


class CRDT(Memory):
    name = "crdt"
    description = "a shared CRDT: an observed-remove set of text items plus last-writer-wins registers; concurrent add and remove of the same item keeps the item"
    reads = ("crdt_read",)
    writes = ("crdt_add", "crdt_remove", "crdt_set")
    destructive = ("crdt_remove",)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.adds = {}
        self.tomb = []
        self.regs = {}

    def specs(self):
        return [
            tool("crdt_read", "Read the set and registers."),
            tool("crdt_add", "Add an item to the set.", {"item": P("string", "item text")}, ["item"]),
            tool("crdt_remove", "Remove an item (all copies you have observed).", {"item": P("string", "item text")}, ["item"]),
            tool("crdt_set", "Set a register (last writer wins).", {"key": P("string", "register"), "value": P("string", "value")}, ["key", "value"]),
        ]

    def live(self):
        return {t: a for t, a in self.adds.items() if t not in self.tomb}

    def prepare(self, op, args, agent):
        if op == "crdt_remove" and "observed" not in args:
            args = dict(args)
            args["observed"] = sorted(t for t, a in self.live().items() if norm(a["item"]) == norm(args.get("item", "")) and (self.acl == "open" or a["author"] == agent))
        return args

    def op_crdt_read(self, agent, rnd):
        items = {}
        for t, a in self.live().items():
            items.setdefault(a["item"], []).append(a["author"])
        return {"items": [{"item": k, "added_by": sorted(set(v))} for k, v in items.items()][:100], "registers": self.regs}

    def op_crdt_add(self, agent, rnd, item):
        t = self.next_id("t")
        self.adds[t] = {"item": self.clip(item), "author": agent}
        return {"tag": t}

    def op_crdt_remove(self, agent, rnd, item, observed=None):
        if observed is None:
            observed = self.prepare("crdt_remove", {"item": item}, agent)["observed"]
        n = 0
        for t in observed:
            if t in self.adds and t not in self.tomb:
                self.own(self.adds[t]["author"], agent)
                self.touch_other(self.adds[t]["author"], agent)
                self.tomb.append(t)
                n += 1
        self.seq += 1
        return {"removed": n}

    def op_crdt_set(self, agent, rnd, key, value):
        rank = self.agents.index(agent) if agent in self.agents else 0
        ts = [rnd, self.seq + 1, rank]
        cur = self.regs.get(str(key))
        if cur:
            self.own(cur["author"], agent)
            self.touch_other(cur["author"], agent)
        if cur is None or ts > cur["ts"]:
            self.regs[str(key)] = {"value": self.clip(value), "author": agent, "ts": ts}
        self.seq += 1
        return {}

    def op_critique(self, agent, rnd, target, text):
        return self.op_crdt_add(agent, rnd, f"[CRITIQUE {target}] {text}")

    def state(self):
        return {"seq": self.seq, "adds": self.adds, "tomb": sorted(self.tomb), "regs": self.regs}

    def extra(self):
        live = self.live()
        return {"set_live": len(live), "set_tombstoned": len(self.tomb), "registers": len(self.regs), "contested": sum(1 for a in live.values() if a["item"].startswith("[CRITIQUE"))}

    def records(self):
        out = [(a["author"], a["item"]) for a in self.live().values()]
        out += [(r["author"], f"{k} = {r['value']}") for k, r in sorted(self.regs.items())]
        return out
