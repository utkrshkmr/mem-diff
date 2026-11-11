from . import factfind, puzzle, codefix
from .base import allocate, Instance

TASKS = {"factfind": factfind, "puzzle": puzzle, "codefix": codefix}


def generate(name, seed):
    return TASKS[name].generate(seed)


def score(inst, item, answer):
    return TASKS[inst.task].score(item, answer)
