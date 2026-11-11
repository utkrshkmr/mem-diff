class Staged:
    def __init__(self, mem, order):
        self.mem = mem
        self.order = list(order)
        self.snap = None
        self.pending = []

    def begin(self):
        self.snap = self.mem.snapshot()
        self.snap.log = []
        self.pending = []

    def apply(self, op, args, agent, rnd, role):
        if op in self.mem.reads:
            r = self.snap.apply(op, args, agent, rnd, role, record=False)
            self.mem.log.append({"op": op, "args": dict(args or {}), "agent": agent, "round": rnd, "role": role, "kind": "read", "ok": bool(r.get("ok")), "cross": False, "hash": None})
            return r
        args = self.snap.prepare(op, dict(args or {}), agent)
        self.pending.append((agent, len(self.pending), op, args, role))
        return {"ok": True, "staged": True, "note": "write staged; it is applied at the end of the round and its result reported next turn"}

    def commit(self, rnd):
        pri = self.order[rnd % len(self.order):] + self.order[: rnd % len(self.order)]
        items = sorted(self.pending, key=lambda x: (pri.index(x[0]), x[1]))
        report = {a: [] for a in self.order}
        for agent, _, op, args, role in items:
            r = self.mem.apply(op, args, agent, rnd, role)
            report[agent].append({"op": op, "args": args, "result": r})
        self.pending = []
        return report
