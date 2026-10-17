"""Exception types raised by the Dervo harness substrate."""


class DervoError(Exception):
    """Base class for every harness error."""


class NotFound(DervoError):
    """A project, room, message, handoff, or skill does not exist."""


class ValidationError(DervoError):
    """Caller supplied malformed or missing input."""


class AccessDenied(DervoError):
    """The caller lacks the explicit membership/role/grant the action needs."""


class PolicyViolation(DervoError):
    """The action is well-formed and the caller is known, but a transfer rule refuses it."""


class StateError(DervoError):
    """The record is not in a status that permits the requested transition."""
