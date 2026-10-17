"""One admission layer for model-requested delegation, shared by every host that lets a running worker ask a named helper.

Two hosts use it today. The :class:`~dervo_harness.supervisor.Supervisor` brokers text-only helpers over rooms: its
sources are message and decision refs and its helper identities are actor ids. A coding host brokers helpers that are
coding tasks: its sources are the granted file paths and its helper identities are agent names. Each host keeps its own
authority checks (who exists, who is bound, granted or linked, which files are in scope) and its own effects (packets,
runs, tasks). What neither host may reimplement is here:

- the **shape** of an ask (:meth:`DelegationRules.shape`, :meth:`DelegationRules.sources`): one handle token, a
  bounded purpose with invisible characters stripped, bounded instructions, one to N distinct sources;
- its **place in the chain** and the **budgets** (:meth:`DelegationRules.decide`): the model-requested depth against
  ``max_depth``, the asks already made by this run and under the original request against ``max_per_run`` and
  ``max_per_root``, the asker asking itself, and a target already in the chain (a loop);
- the **repeat** guard: the same run asking the same helper about the same sources again returns the earlier ask when
  purpose and instructions are identical, and is refused otherwise unless the policy says
  ``allow_repeat_sources`` (a host whose sources are file snapshots that helpers work on may allow a new purpose);
- **admission** (:meth:`DelegationRules.admit`): whether the ask may start now, must wait, or (for a nested ask) is
  refused instead of waiting, given ``max_parallel`` and whether the target is busy. A host with a waiting room queues
  a waiting root ask; a host without one refuses it and says so. A nested ask never waits, so a chain cannot deadlock.

Every refusal is an exception (:class:`ValidationError` for shape, :class:`PolicyViolation` for policy) carrying the
one text both hosts show. The layer holds no state and touches no store: the host reads its own durable records and
hands them in as plain values, so what it decides is exactly what it was told.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from .addressing import normalize_handle
from .errors import PolicyViolation, ValidationError
from .models import DelegationPolicy
from .upstream_adapted import strip_invisible_unicode

ADMIT_START = "start"  # the ask may start now
ADMIT_WAIT = "wait"  # a root ask may wait for a slot or for the target; the host queues it or refuses it, and says which
ADMIT_EXISTING = "existing"  # an identical ask already exists in this run; nothing new is created


@dataclass(frozen=True)
class Ask:
    """One structured ask, already shaped. ``target`` is the handle shown in messages; ``target_id`` is the identity the
    loop guard compares against the chain (an actor id, or the handle itself when the host has no other identity)."""

    target: str
    target_id: str
    purpose: str
    instructions: str
    sources: Tuple[str, ...]


@dataclass(frozen=True)
class Lineage:
    """Where the asking run sits: model-requested hops above it, the original request, the identities above it, itself."""

    depth: int
    root_id: str
    run_id: str
    ancestors: Tuple[str, ...]
    asker: str


@dataclass(frozen=True)
class PriorAsk:
    """An ask this run already made (dispatched, not refused), as the host recorded it."""

    id: str
    target_id: str
    purpose: str
    instructions: str
    sources: Tuple[str, ...]


@dataclass(frozen=True)
class Admission:
    action: str
    depth: int
    existing_id: Optional[str] = None
    reason: str = ""


def _count(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{label} must be a non-negative integer")
    return value


class DelegationRules:
    """The policy checks of one :class:`DelegationPolicy`, as pure functions of what the host hands in."""

    def __init__(self, policy: DelegationPolicy) -> None:
        if not isinstance(policy, DelegationPolicy):
            raise ValidationError("DelegationRules takes a DelegationPolicy")
        self.policy = policy

    # ------------------------------------------------------------------ shape
    def shape(self, target: Any, purpose: Any, instructions: Any) -> Tuple[str, str, str]:
        """``(handle, purpose, instructions)`` normalised and bounded, or :class:`ValidationError` naming the field."""
        handle = normalize_handle(target)
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValidationError("purpose must be a non-empty string")
        purpose = " ".join(strip_invisible_unicode(purpose).split())
        if len(purpose) > self.policy.max_purpose_chars:
            raise ValidationError(f"purpose exceeds {self.policy.max_purpose_chars} characters")
        if not isinstance(instructions, str):
            raise ValidationError("instructions must be a string")
        instructions = strip_invisible_unicode(instructions).strip()
        if len(instructions) > self.policy.max_instruction_chars:
            raise ValidationError(f"instructions exceed {self.policy.max_instruction_chars} characters")
        return handle, purpose, instructions

    def sources(self, refs: Any) -> Tuple[str, ...]:
        """One to ``max_source_refs`` distinct source identities, in the order given."""
        if not isinstance(refs, (list, tuple)) or not refs:
            raise ValidationError("source_refs must list at least one source; a help request carries what it is about")
        out: list = []
        for ref in refs:
            text = str(ref)
            if text not in out:
                out.append(text)
        if len(out) > self.policy.max_source_refs:
            raise ValidationError(f"a help request carries at most {self.policy.max_source_refs} source refs; {len(out)} given")
        return tuple(out)

    # ------------------------------------------------------------------ policy
    def decide(self, ask: Ask, lineage: Lineage, *, per_run: int, per_root: int, priors: Sequence[PriorAsk], active: int,
               target_busy: bool) -> Admission:
        """Depth, budgets, self, loop, repeat, then admission. Raises on refusal; returns what the host must do next."""
        if not isinstance(ask, Ask) or not isinstance(lineage, Lineage):
            raise ValidationError("decide takes an Ask and a Lineage")
        per_run, per_root = _count(per_run, "per_run"), _count(per_root, "per_root")
        if not all(isinstance(p, PriorAsk) for p in priors):
            raise ValidationError("priors must be PriorAsk records")
        policy = self.policy
        depth = lineage.depth + 1
        if depth > policy.max_depth:
            raise PolicyViolation(f"delegation depth {depth} exceeds max_depth {policy.max_depth}: {lineage.asker} is itself a helper "
                                  f"at depth {lineage.depth} under request {lineage.root_id}")
        if per_run >= policy.max_per_run:
            raise PolicyViolation(f"run {lineage.run_id} has used its budget of {policy.max_per_run} help request(s) per run")
        if per_root >= policy.max_per_root:
            raise PolicyViolation(f"request {lineage.root_id} has used its budget of {policy.max_per_root} help request(s) "
                                  "per original request across the whole chain")
        if ask.target_id == lineage.asker:
            raise ValidationError(f"a run may not ask itself for help: @{ask.target} is {lineage.asker}")
        if ask.target_id in lineage.ancestors:
            chain = " -> ".join([*lineage.ancestors, lineage.asker])
            raise PolicyViolation(f"asking @{ask.target} would close a loop: {ask.target_id} is already in this chain ({chain})")
        wanted = sorted(ask.sources)
        for prior in priors:
            if prior.target_id == ask.target_id and sorted(prior.sources) == wanted:
                if prior.purpose == ask.purpose and prior.instructions == ask.instructions:
                    return Admission(ADMIT_EXISTING, depth, existing_id=prior.id)
                if not policy.allow_repeat_sources:
                    raise PolicyViolation(f"duplicate: run {lineage.run_id} already asked @{ask.target} about these sources as delegation "
                                          f"{prior.id} with a different purpose or instructions")
        return self.admit(depth, ask.target, active=active, target_busy=target_busy)

    def admit(self, depth: int, target: str, *, active: int, target_busy: bool) -> Admission:
        """Start now, wait (root asks only), or refuse a nested ask that would have to wait."""
        depth, active = _count(depth, "depth"), _count(active, "active")
        if not isinstance(target_busy, bool):
            raise ValidationError("target_busy must be a bool")
        policy = self.policy
        if policy.max_parallel is not None and active >= policy.max_parallel:
            reason = f"parallel limit of {policy.max_parallel} reached"
        elif target_busy:
            reason = f"@{target} is busy with another ask"
        else:
            return Admission(ADMIT_START, depth)
        if depth > 1:
            raise PolicyViolation(f"{reason}; a nested ask at depth {depth} does not wait")
        return Admission(ADMIT_WAIT, depth, reason=reason)
