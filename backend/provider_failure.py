"""Which run failures are the provider's side: an API call that failed, an overloaded or unreachable service, or a usage or
rate limit. These are the failures where trying your account on the other provider can help. A failed check, a refused
file, a sign-in problem or a policy block is not one of them; switching providers wouldn't fix those.

Claude Code reports a failed API call as its own synthetic assistant message (claude_coding.ClaudeCodingAdapter.api_error).
Codex reports a structured ``codexErrorInfo`` on the failed turn (schema/v2 TurnError); only the kinds below count.
"""
import re

CLAUDE_API_ERROR = 'api_error'
# How Claude Code words an account that's out of allowance: the prefixes CLI 2.1.280 itself uses to tell a limit message
# from other errors ("You've hit your session limit · resets 3pm (Australia/Brisbane)"), plus the older
# "Claude AI usage limit reached|<epoch>". Read from the published CLI on 23 Sep 2026, not from a live limit.
CLAUDE_LIMIT = re.compile(r"^\s*(?:API Error:\s*)?(?:You(?:'|’)ve hit your |You(?:'|’)ve reached your |You(?:'|’)re out of usage credits"
                          r"|You(?:'|’)re out of extra usage|Your org is out of usage|Claude AI usage limit reached)", re.I)
CODEX_KINDS = {
    'usageLimitExceeded': 'usage_limit',
    'rateLimitExceeded': 'rate_limit',
    'serverOverloaded': 'overloaded',
    'internalServerError': 'server_error',
}
CODEX_CONNECTION = ('httpConnectionFailed', 'responseStreamConnectionFailed', 'responseStreamDisconnected',
                    'responseTooManyFailedAttempts')
KINDS = {CLAUDE_API_ERROR, 'usage_limit', 'rate_limit', 'overloaded', 'server_error', 'connection'}


def claude(text, rejected=False):
    """The provider-failure kind of Claude Code's own API-error report: 'usage_limit' when it says the account is out of
    allowance, or when a rate-limit event earlier in the same run said so (``rejected``); otherwise 'api_error'. Before
    23 Sep every Claude failure was 'api_error', so "start when it resets" and the limit wording only worked on Codex
    (systems report, problem 2)."""
    if rejected or (isinstance(text, str) and CLAUDE_LIMIT.match(text)):
        return 'usage_limit'
    return CLAUDE_API_ERROR


def codex(error):
    """The provider-failure kind of a Codex TurnError, or None when it isn't one."""
    if not isinstance(error, dict):
        return None
    info = error.get('codexErrorInfo')
    if isinstance(info, str):
        return CODEX_KINDS.get(info)
    if isinstance(info, dict) and len(info) == 1 and next(iter(info)) in CODEX_CONNECTION:
        return 'connection'
    return None
