from .stores import Scratchpad, KV, Blackboard, TemporalKG, EventLog, TupleSpace, Datalog, CRDT
from .staged import Staged

REGISTRY = {c.name: c for c in (Scratchpad, KV, Blackboard, TemporalKG, EventLog, TupleSpace, Datalog, CRDT)}


def make(name, **kw):
    return REGISTRY[name](**kw)
