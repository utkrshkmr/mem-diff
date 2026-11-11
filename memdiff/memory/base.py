import copy
import hashlib
import json


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def norm(s):
    return " ".join(str(s).strip().casefold().split())


def P(type_, desc, **extra):
    d = {"type": type_, "description": desc}
    d.update(extra)
    return d


def tool(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props or {},
                "required": required or [],
            },
        },
    }


class Reject(Exception):
    pass


class Memory:
    name = "base"
    description = ""
    reads = ()
    writes = ()
    destructive = ()
    max_text = 600

    def __init__(self, acl="open", schema=None, functional=None):
        self.acl = acl
        self.schema = list(schema) if schema else None
        self.functional = set(functional or [])
        self.seq = 0
        self.log = []
        self.agents = []

    def specs(self):
        return []

    def critique_spec(self):
        return tool(
            "critique",
            "Post a critique of a specific memory item or claim.",
            {"target": P("string", "id or short reference of what is criticized"), "text": P("string", "the critique")},
            ["target", "text"],
        )

    def tools(self, role):
        out = []
        for t in self.specs():
            n = t["function"]["name"]
            if role == "critic" and n not in self.reads:
                continue
            out.append(t)
        out.append(self.critique_spec())
        return out

    def next_id(self, prefix):
        self.seq += 1
        return f"{prefix}{self.seq}"

    def clip(self, s):
        s = str(s)
        return s if len(s) <= self.max_text else s[: self.max_text]

    def own(self, author, agent):
        if self.acl == "owner" and author != agent:
            raise Reject("permission denied: item owned by another agent")

    def prepare(self, op, args, agent):
        return args

    def apply(self, op, args, agent, rnd, role="solver", record=True):
        args = dict(args or {})
        allowed = set(self.reads) | set(self.writes) | {"critique"}
        if op not in allowed:
            res = {"ok": False, "error": f"unknown tool {op}"}
            kind = "error"
        elif role == "critic" and op not in self.reads and op != "critique":
            res = {"ok": False, "error": "critic may only read and critique"}
            kind = "denied"
        else:
            fn = getattr(self, "op_" + op)
            self._cur = {"agent": agent, "round": rnd, "other": False}
            try:
                res = fn(agent=agent, rnd=rnd, **args)
                if not isinstance(res, dict):
                    res = {"result": res}
                res.setdefault("ok", True)
                kind = "read" if op in self.reads else "write"
            except Reject as e:
                res = {"ok": False, "error": str(e)}
                kind = "denied"
            except TypeError as e:
                res = {"ok": False, "error": f"bad arguments: {e}"}
                kind = "error"
            except (KeyError, ValueError, IndexError) as e:
                res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                kind = "error"
        if record:
            self.log.append(
                {
                    "op": op,
                    "args": args,
                    "agent": agent,
                    "round": rnd,
                    "role": role,
                    "kind": kind,
                    "ok": bool(res.get("ok")),
                    "cross": bool(getattr(self, "_cur", {}).get("other")) and res.get("ok", False),
                    "hash": self.state_hash() if kind == "write" and res.get("ok") else None,
                }
            )
        return res

    def touch_other(self, author, agent):
        if author != agent:
            self._cur["other"] = True

    def state(self):
        raise NotImplementedError

    def state_hash(self):
        return hashlib.sha256(canon(self.state()).encode()).hexdigest()[:16]

    def records(self):
        return []

    def extra(self):
        return {}

    def structure(self):
        recs = self.records()
        by = {}
        for a, _ in recs:
            by[a] = by.get(a, 0) + 1
        w = [e for e in self.log if e["kind"] == "write" and e["ok"]]
        d = {
            "records": len(recs),
            "records_A": by.get("A", 0),
            "records_B": by.get("B", 0),
            "state_bytes": len(canon(self.state())),
            "writes": len(w),
            "reads": sum(e["kind"] == "read" for e in self.log),
            "destructive": sum(e["op"] in self.destructive for e in w),
            "cross": sum(bool(e["cross"]) for e in w),
            "critiques": sum(e["op"] == "critique" for e in w),
            "denied": sum(e["kind"] == "denied" for e in self.log),
            "errors": sum(e["kind"] == "error" for e in self.log),
            "contested": 0,
        }
        d["churn"] = d["destructive"] / d["writes"] if d["writes"] else 0.0
        d.update(self.extra())
        return d

    def fresh(self):
        m = type(self)(acl=self.acl, schema=self.schema, functional=self.functional)
        m.agents = list(self.agents)
        return m

    def replay_check(self):
        m = self.fresh()
        for e in self.log:
            if e["kind"] == "write" and e["ok"]:
                r = m.apply(e["op"], e["args"], e["agent"], e["round"], e["role"])
                if not r.get("ok") or m.state_hash() != e["hash"]:
                    return False
        return m.state_hash() == self.state_hash()

    def snapshot(self):
        return copy.deepcopy(self)
