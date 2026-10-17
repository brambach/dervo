"""Which Mac icons this account may use (beta.15, group 7), for Settings > Preferences > App icon and for the native
app_icon_set command (src/app_icon.rs), which asks ``_app_icon_check`` before it saves a choice.

Nine icons are free for everyone. Obsidian, the founder icon, is for testers approved before the public launch (Bryce,
24 Sep 2026). It's tied to the account, not the Mac: the hosted ``dervo_is_founder()`` answers for the signed-in person
(supabase/migrations/202609240002_dervo_founders.sql), so a reinstall or a new Mac keeps it, and the last answer for
that account is kept on this Mac so the icon stays while offline. Dervo is going open source, so treat it as a gift,
not a lock: the choice lives in Dervo's own preferences, and editing those only changes the Dock picture.

The check runs on its own thread and never holds the host. Nothing here writes to the hosted database.
"""
import threading
import time

from dervo_connections import Rejected

FIELDS = {'app_icon_status': set(), '_app_icon_check': {'icon'}}
# The same ids, in the same order, as icons/choices/choices.json and src/app_icon.rs (test_app_icons checks both).
ICONS = (('cream', 'Cream'), ('forest', 'Forest'), ('meadow', 'Meadow'), ('peek', 'Peek'), ('crew', 'Crew'),
         ('terminal', 'Terminal'), ('pixel', 'Pixel'), ('night', 'Night'), ('clay', 'Clay'), ('obsidian', 'Obsidian'))
FOUNDER = 'obsidian'
RECHECK_SECONDS = 3600
DETAIL = {
    'founder': 'You were here before launch, so Obsidian is yours for good, on any Mac you sign in on.',
    'not_founder': 'Obsidian is for testers who joined before the public launch.',
    'checking': 'Checking whether Obsidian is yours…',
    'unknown': 'Dervo couldn’t check whether Obsidian is yours. It tries again next time you open this page.',
    'signed_out': 'Sign in to your Dervo account to see whether Obsidian is yours.',
}


class AppIcons:
    def __init__(self, host, *, clock=time.time, fetch=None):
        self.host, self.clock = host, clock
        self.fetch = fetch or self._fetch
        self.lock = threading.Lock()
        self.running = None        # the subject a background check is running for
        self.answers = {}          # subject -> (founder or None for a failed check, when)
        self.db = getattr(getattr(host, 'store', None), 'db', None)
        if self.db is not None:
            with self.db:
                self.db.execute('create table if not exists app_icon_founders (subject text primary key, '
                                'founder integer not null check (founder in (0, 1)), checked_at real not null)')

    # ------------------------------------------------------------------ account
    def _account(self):
        """(account, subject) for a verified signed-in session, or None. No membership check: the founder flag stays
        with the account whatever happens to its beta access."""
        account = getattr(self.host, 'account_session', None)
        if account is None:
            return None
        try:
            session = account.session
            identity = account.status().get('identity')
        except Exception:  # noqa: BLE001 - a broken session reads as signed out
            return None
        if (not session or type(identity) is not dict or type(identity.get('subject')) is not str
                or identity['subject'] != getattr(session, 'user_id', None)):
            return None
        return account, identity['subject']

    @staticmethod
    def _fetch(account):
        session = account.session
        result = account.client._request('POST', '/rest/v1/rpc/dervo_is_founder', {}, session.access_token)
        if type(result) is not bool:
            raise ValueError('dervo_is_founder returned something other than true or false')
        return result

    # ------------------------------------------------------------------ this Mac's record
    def _saved(self, subject):
        if self.db is None:
            return None
        row = self.db.execute('select founder from app_icon_founders where subject=?', (subject,)).fetchone()
        return None if row is None else bool(row[0])

    def _save(self, subject, founder):
        if self.db is None:
            return
        with self.db:
            self.db.execute('insert into app_icon_founders values (?,?,?) on conflict(subject) do update set '
                            'founder=excluded.founder, checked_at=excluded.checked_at', (subject, int(founder), self.clock()))

    # ------------------------------------------------------------------ the check
    def _start(self, account, subject):
        self.running = subject

        def run():
            try:
                answer = self.fetch(account)
            except Exception:  # noqa: BLE001 - offline, not applied yet or a bad answer: unknown, never a guess
                answer = None
            with self.lock:
                self.answers[subject] = (answer, self.clock())
                self.running = None
        threading.Thread(target=run, name='dervo-founder-check', daemon=True).start()

    def _collect(self, subject):
        """Save a finished answer. A failed check keeps whatever this Mac already knew."""
        with self.lock:
            answer = self.answers.get(subject)
        if answer and answer[0] is not None and self._saved(subject) != answer[0]:
            self._save(subject, answer[0])

    def founder(self, *, refresh=True):
        """'founder', 'not_founder', 'checking', 'unknown' or 'signed_out'. Starts a background check at most once an
        hour per account when ``refresh`` is set."""
        found = self._account()
        if not found:
            return 'signed_out'
        account, subject = found
        self._collect(subject)
        with self.lock:
            answer, running = self.answers.get(subject), self.running
        if refresh and running is None and (answer is None or self.clock() - answer[1] >= RECHECK_SECONDS):
            self._start(account, subject)
            running = subject
        saved = self._saved(subject)
        if saved is not None:
            return 'founder' if saved else 'not_founder'
        if running == subject:
            return 'checking'
        return 'unknown'

    # ------------------------------------------------------------------ operations
    def status(self):
        state = self.founder()
        return {'founder': state, 'detail': DETAIL[state],
                'icons': [{'id': icon, 'name': name, 'founder': icon == FOUNDER,
                           'available': icon != FOUNDER or state == 'founder'} for icon, name in ICONS]}

    def check(self, icon):
        if icon not in dict(ICONS):
            return {'allowed': False, 'detail': 'That icon isn’t one Dervo offers.'}
        if icon != FOUNDER:
            return {'allowed': True, 'detail': ''}
        state = self.founder(refresh=False)
        return {'allowed': state == 'founder', 'detail': '' if state == 'founder' else DETAIL[state]}

    def dispatch(self, operation, args):
        if operation not in FIELDS or type(args) is not dict or set(args) != FIELDS[operation]:
            raise Rejected('Unsupported app icon fields.')
        if operation == 'app_icon_status':
            return self.status()
        if type(args['icon']) is not str:
            raise Rejected('Choose an icon from the list.')
        return self.check(args['icon'])
