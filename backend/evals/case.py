"""One eval case: setup (seed records, repo files, messages), action, checker.

``setup(h, ctx)`` builds the world on a fresh EvalHost and puts what the action needs in ``ctx``. ``action(h, ctx)``
returns the outcome. ``check(h, ctx, outcome)`` returns ``(passed, reason)``; the reason is one line, the one the
baseline table shows. Checks are exact where the record is exact and keyword or structure checks on model text; no
model judges anything.
"""
from dataclasses import dataclass, field
from typing import Callable

SYSTEMS = ('memory', 'sorting', 'home', 'handoff')


@dataclass
class Case:
    id: str
    system: str
    title: str
    setup: Callable
    action: Callable
    check: Callable
    provider: str = 'none'  # 'none' (rules or fake only), 'claude', 'codex', 'home' (Home's own Claude call)
    calls: int = 0  # real model calls the case makes, for the budget guard
    source: str = ''  # which review sketch row this comes from
    expect_fail: bool = False  # the review says today's code gives the wrong answer; recorded, not hidden
    tags: tuple = field(default_factory=tuple)

    @property
    def needs_model(self):
        return self.provider != 'none'


REGISTRY = {system: [] for system in SYSTEMS}


def case(id, system, title, *, provider='none', calls=0, source='', expect_fail=False, tags=()):
    """Decorator form: the function returns (setup, action, check). The case is registered by system; a case module
    ends with ``CASES = REGISTRY['<system>']``."""
    def wrap(build):
        setup, action, check = build()
        made = Case(id, system, title, setup, action, check, provider=provider, calls=calls, source=source, expect_fail=expect_fail, tags=tuple(tags))
        if any(existing.id == id for existing in REGISTRY[system]):
            raise ValueError(f'duplicate case id {id}')
        REGISTRY[system].append(made)
        return made
    return wrap
