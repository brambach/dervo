"""Usage per account, from what each provider reports. Nothing is estimated by Dervo itself.

* Claude Code's ``result`` message carries ``usage`` token counts and ``total_cost_usd``, a client-side estimate in
  Claude Code's own words. Its stream also carries a ``rate_limit_event`` whose ``rate_limit_info`` has the subscription's
  windows: ``unifiedWindows.five_hour`` and ``.seven_day``, each a ``utilization`` fraction and ``resetsAt`` (seen from
  Claude Code 2.1.274 on 17 September 2026), or a single ``utilization`` with ``rateLimitType``. ``claude_rate_limits``
  maps them onto the Codex window shape, so both providers' bars read the same way.
* Codex's app-server sends ``thread/tokenUsage/updated`` during a turn and ``account/rateLimits/updated`` when the
  account's windows move; ``account/rateLimits/read`` returns the same snapshot on demand. A window has ``usedPercent``,
  ``resetsAt`` and ``windowDurationMins``. That percentage is what a Codex subscription's bar shows.
* An API-key account's bar is its estimated spend this month against the monthly limit chosen when the key was added.
  The limit is only shown: nothing stops a run at it, helper runs aren't recorded here, and Codex reports no cost, so a
  Codex key never gets a spend percentage (security review, 19 Sep 2026, finding 2). The copy says so.
* Home's own replies are recorded too (23 Sep 2026), each under HOME_TASK plus its project and the reply's id: their
  tokens and cost count toward the account, but they aren't runs, so the run counts leave them out. Home asks for json
  output, which carries no rate-limit windows, so its calls never move a subscription's window by themselves.

Every row is tied to a task run or a Home reply, so a summary is a plain aggregate over saved facts.
"""
import json
import time

RATE_LIMIT_FIELDS = ('usedPercent', 'resetsAt', 'windowDurationMins')
HOME_TASK = 'home:'  # the task id prefix of Home's own replies: counted in tokens and cost, never as runs
RUNS = "coalesce(sum(task_id NOT LIKE 'home:%'), 0)"
LOW_PERCENT = 80


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS provider_usage (task_id TEXT NOT NULL, run_id TEXT NOT NULL, profile_id TEXT NOT NULL, '
               'provider TEXT NOT NULL, method TEXT, at INTEGER NOT NULL, input_tokens INTEGER, output_tokens INTEGER, '
               'total_tokens INTEGER, cost_usd REAL, PRIMARY KEY(task_id, run_id))')
    db.execute('CREATE INDEX IF NOT EXISTS provider_usage_profile ON provider_usage(profile_id, at)')
    db.execute('CREATE TABLE IF NOT EXISTS provider_rate_limits (profile_id TEXT PRIMARY KEY, at INTEGER NOT NULL, document TEXT NOT NULL)')


def _int(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def normalise(provider, data):
    """One shape for both providers: input, output and total tokens, and the provider's own cost estimate when it gives one."""
    if not isinstance(data, dict):
        return None
    if provider == 'codex':
        total = (data.get('tokenUsage') or {}).get('total') if isinstance(data.get('tokenUsage'), dict) else None
        if not isinstance(total, dict):
            return None
        inputs, outputs, totals = _int(total.get('inputTokens')), _int(total.get('outputTokens')), _int(total.get('totalTokens'))
        if totals is None and inputs is not None and outputs is not None:
            totals = inputs + outputs
        return {'input_tokens': inputs, 'output_tokens': outputs, 'total_tokens': totals, 'cost_usd': None}
    usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    inputs = _int(usage.get('input_tokens'))
    for key in ('cache_creation_input_tokens', 'cache_read_input_tokens'):
        extra = _int(usage.get(key))
        if extra is not None:
            inputs = (inputs or 0) + extra
    outputs = _int(usage.get('output_tokens'))
    cost = data.get('total_cost_usd')
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0 else None
    if inputs is None and outputs is None and cost is None:
        return None
    return {'input_tokens': inputs, 'output_tokens': outputs,
            'total_tokens': (inputs or 0) + (outputs or 0) if inputs is not None or outputs is not None else None, 'cost_usd': cost}


def record_usage(db, task_id, run_id, binding, usage, at=None):
    """Latest totals for this run; a run that reports twice keeps the later figures."""
    if usage is None:
        return
    with db:
        db.execute('INSERT INTO provider_usage VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(task_id, run_id) DO UPDATE SET '
                   'input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, total_tokens=excluded.total_tokens, '
                   'cost_usd=excluded.cost_usd, at=excluded.at',
                   (task_id, run_id, binding['profile_id'], binding['provider'], binding.get('method'), int(at or time.time()),
                    usage['input_tokens'], usage['output_tokens'], usage['total_tokens'], usage['cost_usd']))


def rate_limit_snapshot(raw):
    """Only the documented window fields, for the primary and secondary windows, plus the plan and spend-control flags."""
    if not isinstance(raw, dict):
        return None
    snapshot = {}
    for name in ('primary', 'secondary'):
        window = raw.get(name)
        if isinstance(window, dict) and _int(window.get('usedPercent')) is not None:
            snapshot[name] = {key: window.get(key) for key in RATE_LIMIT_FIELDS if isinstance(window.get(key), (int, float)) and not isinstance(window.get(key), bool)}
    if isinstance(raw.get('planType'), str):
        snapshot['planType'] = raw['planType']
    if isinstance(raw.get('spendControlReached'), bool):
        snapshot['spendControlReached'] = raw['spendControlReached']
    if isinstance(raw.get('rateLimitReachedType'), str):
        snapshot['rateLimitReachedType'] = raw['rateLimitReachedType']
    return snapshot or None


CLAUDE_WINDOWS = {'five_hour': 300, 'seven_day': 10080}


def _claude_window(utilization, resets_at, minutes):
    if not isinstance(utilization, (int, float)) or isinstance(utilization, bool) or utilization < 0:
        return None
    window = {'usedPercent': min(100, int(round(utilization * 100))), 'windowDurationMins': minutes}
    if _int(resets_at) is not None:
        window['resetsAt'] = int(resets_at)
    return window


def claude_rate_limits(info):
    """Claude Code's ``rate_limit_info`` in the Codex shape: the 5-hour window as primary, the 7-day window as secondary.
    Utilization is a fraction of the window. Anything unrecognised gives None rather than a guess."""
    if not isinstance(info, dict):
        return None
    raw = {}
    windows = info.get('unifiedWindows')
    if isinstance(windows, dict):
        for key, slot in (('five_hour', 'primary'), ('seven_day', 'secondary')):
            entry = windows.get(key)
            if isinstance(entry, dict):
                window = _claude_window(entry.get('utilization'), entry.get('resetsAt'), CLAUDE_WINDOWS[key])
                if window:
                    raw[slot] = window
    if not raw and info.get('rateLimitType') in CLAUDE_WINDOWS:
        used = info.get('utilization')
        if used is None and info.get('status') == 'rejected':
            used = 1
        window = _claude_window(used, info.get('resetsAt'), CLAUDE_WINDOWS[info['rateLimitType']])
        if window:
            raw['primary' if info['rateLimitType'] == 'five_hour' else 'secondary'] = window
    if info.get('status') == 'rejected':
        raw['rateLimitReachedType'] = 'rate_limit_reached'
    return raw if raw.get('primary') or raw.get('secondary') else None


def windows_now(rate, now):
    """The tightest window that hasn't reset yet: (used percent, resets at, window minutes). A window whose reset time has
    passed counts as unused, because the snapshot predates the reset. None when the snapshot has no windows."""
    if not isinstance(rate, dict):
        return None
    best = None
    for name in ('primary', 'secondary'):
        window = rate.get(name)
        if not isinstance(window, dict) or _int(window.get('usedPercent')) is None:
            continue
        resets = window.get('resetsAt')
        used = 0 if isinstance(resets, (int, float)) and resets <= now else int(window['usedPercent'])
        candidate = (used, resets if isinstance(resets, (int, float)) and resets > now else None, window.get('windowDurationMins'))
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best


def record_rate_limits(db, profile_id, raw, at=None):
    snapshot = rate_limit_snapshot(raw)
    if snapshot is None or not isinstance(profile_id, str):
        return None
    with db:
        db.execute('INSERT INTO provider_rate_limits VALUES (?,?,?) ON CONFLICT(profile_id) DO UPDATE SET at=excluded.at, document=excluded.document',
                   (profile_id, int(at or time.time()), json.dumps(snapshot)))
    return snapshot


def _day_start(now):
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def _month_start(now):
    local = time.localtime(now)
    return int(time.mktime((local.tm_year, local.tm_mon, 1, 0, 0, 0, 0, 0, -1)))


def summary(db, now=None):
    """Every account with what its provider reported: today's runs and tokens, this month's estimated cost, the Codex
    rate-limit windows when known, and the API-key spend against its limit. ``percent`` is None when nothing honest fills it."""
    now = now or time.time()
    day, month = _day_start(now), _month_start(now)
    accounts = []
    for row in db.execute('SELECT id, provider, identity, category, method, monthly_limit_usd, name FROM profiles WHERE removed_at IS NULL ORDER BY rowid'):
        profile = dict(row)
        today = db.execute(f'SELECT {RUNS}, coalesce(sum(total_tokens), 0), coalesce(sum(cost_usd), 0), sum(cost_usd IS NOT NULL) '
                           'FROM provider_usage WHERE profile_id=? AND at>=?', (profile['id'], day)).fetchone()
        this_month = db.execute(f'SELECT coalesce(sum(cost_usd), 0), {RUNS}, sum(cost_usd IS NOT NULL) FROM provider_usage WHERE profile_id=? AND at>=?',
                                (profile['id'], month)).fetchone()
        limits = db.execute('SELECT at, document FROM provider_rate_limits WHERE profile_id=?', (profile['id'],)).fetchone()
        rate = json.loads(limits['document']) if limits else None
        keyed = profile.get('method') == 'api_key'
        limit = profile.get('monthly_limit_usd')
        cost_month = float(this_month[0]) if this_month[2] else None
        percent = None
        window = windows_now(rate, now)
        if window:
            percent = window[0]
        elif keyed and limit and cost_month is not None:
            percent = min(100, int(round(cost_month / limit * 100)))
        accounts.append({
            'profile_id': profile['id'], 'provider': profile['provider'], 'identity': profile['identity'],
            # The name you gave the account on Connected accounts, or None. The picker and the sidebar read this field.
            'name': profile.get('name'),
            'method': profile.get('method') or 'subscription', 'monthly_limit_usd': limit,
            'runs_today': int(today[0]), 'tokens_today': int(today[1]),
            'cost_today_usd': float(today[2]) if today[3] else None, 'cost_month_usd': cost_month, 'runs_month': int(this_month[1]),
            'rate_limits': rate, 'rate_limits_at': limits['at'] if limits else None,
            'windows': [dict(percent=w[0], resets_at=w[1], window_mins=w[2])
                        for slot in ('primary', 'secondary')
                        if (w := windows_now({'primary': (rate or {}).get(slot)}, now))],
            'resets_at': window[1] if window else None, 'window_mins': window[2] if window else None,
            'percent': percent, 'low': percent is not None and percent >= LOW_PERCENT,
            'basis': 'rate_limits' if window else 'spend_limit' if keyed and limit else 'runs'})
    return {'at': int(now), 'accounts': accounts}
