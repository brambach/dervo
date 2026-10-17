"""Which Claude plan each account is on, from the CLI's own ``auth status``.

On Pro and Team standard seats, Claude Code bills every Fable message to paid usage credits. In an interactive terminal
it asks first; in print mode, which is how Dervo runs, it doesn't (PLAN-native.md, A4). So Dervo keeps the plan it saw
last. The model picker marks Pro as "Paid credits" and Team as "Credits may apply", since auth status doesn't identify
the Team seat. Its notice explains Premium's included allowance and Standard's credits before a thread switches.

The plan is recorded wherever Dervo already reads ``auth status``: the connection check, first run, and the start of
every Claude run. Nothing here calls the CLI. Its own table, so the tables beta.7 writes by position stay as they are.
"""
import time

from dervo_connections import Rejected

# Team alone doesn't establish credit billing: Premium includes a Fable allowance and Standard doesn't.
# Source: support.claude.com/en/articles/15424964 (Claude Fable models on your plan), checked 22 Sep 2026.
PAID_CREDIT_PLANS = ('pro',)


# The one operation: you acknowledged Fable's usage notice for this account, so the picker and project settings don't ask
# again (project-settings/interactions.md: "Dervo asks once for Personal").
FIELDS = {'profile_fable_ack': {'profileId'}}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS profile_plans (profile_id TEXT PRIMARY KEY, plan TEXT NOT NULL, at INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS fable_acks (profile_id TEXT PRIMARY KEY, at INTEGER NOT NULL)')


def plan_of(metadata):
    """The plan string in ``auth status`` metadata, or None when it isn't a short plain string."""
    plan = metadata.get('subscriptionType') if isinstance(metadata, dict) else None
    if type(plan) is not str or not plan or len(plan) > 32 or not plan.isascii() or not plan.isprintable():
        return None
    return plan


def record(db, profile_id, metadata, at=None):
    """Keep the plan ``auth status`` reported for this profile. Returns the plan, or None when there was none to keep."""
    plan = plan_of(metadata)
    if plan is None or type(profile_id) is not str or not profile_id:
        return None
    schema(db)
    with db:
        db.execute('INSERT INTO profile_plans VALUES (?,?,?) ON CONFLICT(profile_id) DO UPDATE SET plan=excluded.plan, at=excluded.at',
                   (profile_id, plan, int(at or time.time())))
    return plan


def plans(db):
    """Every recorded plan, by profile id."""
    schema(db)
    return {row[0]: row[1] for row in db.execute('SELECT profile_id, plan FROM profile_plans')}


def fable_costs_credits(plan):
    return plan in PAID_CREDIT_PLANS


def acks(db):
    """The accounts where you've acknowledged Fable's usage notice."""
    schema(db)
    return {row[0] for row in db.execute('SELECT profile_id FROM fable_acks')}


def annotate(db, rows):
    """The profiles listing, with what the model picker needs about each: the plan auth status last reported (A4), and
    whether you've already been asked about Fable's paid credits on that account."""
    known, asked = plans(db), acks(db)
    return [dict(row, plan=known.get(row['id']), fable_confirmed=row['id'] in asked) for row in rows]


def dispatch(host, operation, args):
    if not isinstance(args, dict) or set(args) != FIELDS.get(operation, {None}):
        raise Rejected('Unsupported account fields')
    from connections_ui import owned_profile
    profile_id = args['profileId']
    if type(profile_id) is not str or not profile_id:
        raise Rejected('Choose an account.')
    profile = owned_profile(host.store, host.root, profile_id)  # one of this Mac's own accounts, work or personal
    if profile['provider'] != 'claude':
        raise Rejected('Only a Claude account has Fable to confirm.')
    schema(host.store.db)
    with host.store.db:
        host.store.db.execute('INSERT OR IGNORE INTO fable_acks VALUES (?,?)', (profile_id, int(time.time())))
    return {'id': profile_id, 'fable_confirmed': True}
