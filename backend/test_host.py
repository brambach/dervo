import tempfile
import unittest
from pathlib import Path
import host

class HostTests(unittest.TestCase):
    def test_official_sign_in_instructions_are_owned_and_not_authentication(self):
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                profile = h.dispatch('add_profile', {'provider':'codex','identity':'expected@example.test','category':'personal'})
                plan = h.dispatch('connection_instructions', {'profileId':profile['id']})
                self.assertEqual(plan['state'], 'unchecked')
                self.assertIn('CODEX_HOME=', plan['command'])
                self.assertIn('env -i', plan['command'])
                self.assertIn('cli_auth_credentials_store=', plan['command'])
                self.assertIn(str(Path(root)/'data'/'profiles'/profile['id']), plan['command'])
                self.assertNotIn('--with-', plan['command'])
                self.assertTrue((Path(root)/'data'/'profiles'/profile['id']).is_dir(), 'Sign-in directory must exist')
                self.assertFalse(plan['canSend'])
                with self.assertRaises(ValueError):
                    h.dispatch('connection_instructions', {'profileId':'missing'})
            finally:
                h.close()

    def test_preflight_unknown_never_means_signed_out_or_ready(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                p = h.dispatch('add_profile', {'provider':'codex','identity':'expected@example.test','category':'personal'})['id']
                with patch('connections_ui.probe', create=True, return_value={'state':'unknown','canSend':False,'detail':'Test double: timeout'}):
                    state = h.dispatch('connection_check', {'profileId':p})
                    for _ in range(100):
                        state = h.dispatch('connection_status', {'profileId':p})
                        if state['state'] != 'checking': break
                        import time
                        time.sleep(.001)
                self.assertEqual(state['state'], 'unknown')
                self.assertFalse(state['canSend'])
            finally:
                h.close()

    def test_a_plain_refusal_is_answered_but_not_reported_while_a_real_error_is(self):
        """run-lifecycle.md, finding 12. Every request the host answered {"error"} used to be captured as an error
        report, so an ordinary Rejected ("Unsupported local operation") landed as one. Now only a non-Rejected error is
        reported; the refusal is still answered to the caller."""
        import io, json, os
        import error_reports
        captured = []
        original = error_reports.capture
        error_reports.capture = lambda error, **kw: captured.append((type(error).__name__, kw.get('operation'))) or True
        try:
            with tempfile.TemporaryDirectory() as root:
                h = host.Host(Path(root)/'data')
                try:
                    read_fd, write_fd = os.pipe()
                    os.write(write_fd, (json.dumps({'operation':'no_such_operation','args':{}})+'\n').encode())
                    # A genuine (non-Rejected) error: _picked_project with a missing argument raises TypeError.
                    os.write(write_fd, (json.dumps({'operation':'_picked_project','args':{'path':'/tmp/x'}})+'\n').encode())
                    os.write(write_fd, b'{"operation":"_shutdown","args":{}}\n')
                    os.close(write_fd)
                    out = io.StringIO()
                    host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out)
                    replies = [json.loads(line) for line in out.getvalue().splitlines()]
                    self.assertEqual(replies[0], {'error':'Unsupported operation or fields'})
                    self.assertIn('error', replies[1])
                finally:
                    h.close()
        finally:
            error_reports.capture = original
        self.assertEqual(captured, [('TypeError', '_picked_project')],
                         'only the genuine error is reported; the Rejected refusal is not')

    def test_an_unexpected_error_reaches_the_screen_as_one_plain_line_with_a_reference(self):
        """Review, 23 Sep: a non-Rejected error was answered with str(error), so a raw Python message (a path, a SQL
        statement) reached the screen. Now it's one plain line with a reference; a refusal and a native-only step's own
        wording are answered as before."""
        import io, json, os, re, sys
        from dervo_connections import Rejected
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                def failing(operation, args):
                    if operation == 'status':
                        raise KeyError('/Users/someone/private/project.sqlite3')
                    if operation == '_prepare_update':
                        raise RuntimeError('The downloaded update couldn’t be unpacked. Nothing was installed.')
                    if operation == 'profiles':
                        raise RuntimeError('SELECT document FROM coding_tasks failed at /Users/someone')
                    raise Rejected('This project is archived. Restore it before opening or changing its records.')
                h.dispatch = failing
                read_fd, write_fd = os.pipe()
                for operation in ('status', 'status', '_prepare_update', 'profiles', 'projects'):
                    os.write(write_fd, (json.dumps({'operation': operation, 'args': {}})+'\n').encode())
                os.write(write_fd, b'{"operation":"_shutdown","args":{}}\n')
                os.close(write_fd)
                out, err = io.StringIO(), io.StringIO()
                real_err, sys.stderr = sys.stderr, err
                try:
                    host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out)
                finally:
                    sys.stderr = real_err
                replies = [json.loads(line)['error'] for line in out.getvalue().splitlines()]
            finally:
                h.close()
        line = re.compile(r'Something went wrong in Dervo’s workspace \(ref [0-9a-f]{6}\)\. Try again, and send feedback if it repeats\.')
        self.assertRegex(replies[0], line)
        self.assertNotIn('/Users/someone', replies[0])
        self.assertEqual(replies[0], replies[1], 'the same error gives the same reference')
        self.assertEqual(replies[2], 'The downloaded update couldn’t be unpacked. Nothing was installed.', 'a native step keeps its words')
        self.assertRegex(replies[3], line, 'a renderer operation never shows a RuntimeError’s own text')
        self.assertNotEqual(replies[3], replies[0], 'a different error has a different reference')
        self.assertEqual(replies[4], 'This project is archived. Restore it before opening or changing its records.')
        ref = re.search(r'ref ([0-9a-f]{6})', replies[0]).group(1)
        self.assertIn(f'(ref {ref}): KeyError', err.getvalue(), 'the whole error stays in the log on this Mac')
        self.assertIn('/Users/someone/private', err.getvalue())
        self.assertEqual(err.getvalue().count('(ref ' + ref), 1, 'the same error is logged at most once a minute')
        # 23 Sep: three "Host error" reports couldn't be traced, because a refusal never reached the log and no line had
        # a time. Each line now starts with the local time, and a refusal is one line saying where Dervo refused.
        stamp = r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} '
        self.assertRegex(err.getvalue(), stamp + re.escape(f'A status request failed (ref {ref}): KeyError'))
        self.assertRegex(err.getvalue(), stamp + r'A projects request was refused at test_host\.py:\d+: This project is archived\. Restore it before opening or changing its records\.')

    def test_a_repeating_refusal_is_logged_once_a_minute(self):
        import io, sys
        from dervo_connections import Rejected
        noted, err = {}, io.StringIO()
        real_err, sys.stderr = sys.stderr, err
        try:
            for at in (0, 5, 61):
                try:
                    raise Rejected('Choose a project.\nThen try again.')
                except Rejected as error:
                    host.note_request_refusal(error, 'tasks', noted, now=lambda: at)
        finally:
            sys.stderr = real_err
        lines = err.getvalue().splitlines()
        self.assertEqual(len(lines), 2, 'once at 0 seconds, again after a minute')
        self.assertTrue(lines[0].endswith('A tasks request was refused at test_host.py:' + lines[0].split('test_host.py:')[1].split(':')[0] + ': Choose a project. Then try again.'))

    def test_an_idle_host_waits_for_requests_and_anything_in_flight_keeps_the_old_pace(self):
        """Efficiency review, 23 Sep: serve() woke every 20 ms forever, 42 times a second on an idle host. It now waits
        for a request while nothing is in flight, and keeps the 20 ms pace for anything tick() has to follow."""
        import json, time
        from concurrent.futures import Future
        from types import SimpleNamespace
        from account_config import UnavailableAccount
        with tempfile.TemporaryDirectory() as root:
            # No account: one built from the checkout's config writes its first record on the first tick (PR #25 review).
            h = host.Host(Path(root)/'data', account_runtime=lambda store: UnavailableAccount())
            try:
                h.tick()
                self.assertIsNone(h.in_flight())
                self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                h.coding_tasks(); h.tick()
                self.assertIsNone(h.in_flight(), 'the coordinator of a workspace that has run tasks is not work')
                self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                running = Future()
                cases = [
                    ('coding run', lambda: h.coding.turns.__setitem__('t', SimpleNamespace(closed=False)), lambda: h.coding.turns.clear()),
                    ('automatic retry', lambda: h.coding.conversation.pending_retries.add('t'), lambda: h.coding.conversation.pending_retries.clear()),
                    ('folder read', lambda: h.coding.folder_reads.put(('t', 'r', [])), lambda: h.coding.settle_folder_reads()),
                    ('chat turn', lambda: h.turns.__setitem__('m', SimpleNamespace(closed=False)), lambda: h.turns.clear()),
                    ('sign-in', lambda: h.logins.__setitem__('p', SimpleNamespace(closed=False, state='waiting', identity_scheduled=False)), lambda: h.logins.clear()),
                    ('connection check', lambda: h.checks.__setitem__('p', running), lambda: h.checks.clear()),
                    ('Home call', lambda: setattr(h, 'home_turns', SimpleNamespace(running={'k': running}, close=lambda: None)), lambda: h.home_turns.running.clear()),
                    ('pushes', lambda: setattr(h, '_pushes', SimpleNamespace(futures={'p': ('r', running)}, tick=lambda: None, close=lambda: None)), lambda: h._pushes.futures.clear()),
                    ('updates', lambda: setattr(h, 'updates', SimpleNamespace(future=running, tick=lambda: None, close=lambda: None)), lambda: setattr(h.updates, 'future', None)),
                ]
                for name, start, stop in cases:
                    with self.subTest(name):
                        start()
                        self.assertEqual((h.in_flight(), h.tick_wait()), (name, host.BUSY_WAIT))
                        stop()
                        h.tick()
                        self.assertIsNone(h.in_flight())
                        self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                # A queued message starts as soon as its thread is free, so it keeps the pace; one waiting for a usage
                # reset sleeps until then.
                profile = h.dispatch('add_profile', {'provider':'codex','identity':'expected@example.test','category':'personal'})['id']
                project = h.add_picked_project(root, profile, 'unverified-model')['id']
                channel = h.dispatch('create_conversation', {'projectId':project,'name':'Work','private':False})['id']
                item = dict(id='q', thread_id=channel, project_id=project, client_id='c', args={'text': 'next'}, state='queued', created_at=0)
                with h.store.db:
                    h.coding.conversation.queue._put(item)
                self.assertEqual(h.in_flight(), 'queued message')
                with h.store.db:
                    h.coding.conversation.queue._put({**item, 'not_before': int(time.time()) + 5})
                self.assertIsNone(h.in_flight())
                self.assertTrue(3 < h.tick_wait() <= 5, h.tick_wait())
                with h.store.db:
                    h.coding.conversation.queue._put({**item, 'state': 'failed'})
                self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                # A round that changed a record is followed by another at the old pace; an idle one isn't.
                h.tick_wrote = True
                self.assertEqual(h.tick_wait(), host.BUSY_WAIT)
                h.tick()
                self.assertFalse(h.tick_wrote)
                # Timed work: the account's next check, never past IDLE_WAIT.
                h.account_session = SimpleNamespace(tick_due=lambda: 3.5, tick=lambda: None, close=lambda: None)
                self.assertEqual(h.tick_wait(), 3.5)
                h.account_session = SimpleNamespace(tick_due=lambda: 600, tick=lambda: None, close=lambda: None)
                self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                h.account_session = SimpleNamespace(tick_due=lambda: None, tick=lambda: None, close=lambda: None)
                self.assertEqual(h.tick_wait(), host.IDLE_WAIT)
                # When it can't tell, the old pace.
                h.logins['broken'] = object()
                self.assertEqual(h.tick_wait(), host.BUSY_WAIT)
                h.logins.clear()
                del h.account_session
            finally:
                h.close()

    def test_serve_sleeps_while_idle_and_catches_up_before_a_late_request(self):
        import io, json, os, select, threading, time
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                events, selects = [], [0]
                real_tick, real_dispatch, real_select = h.tick, h.dispatch, select.select
                h.tick = lambda: (events.append('tick'), real_tick())
                h.dispatch = lambda operation, args: (events.append(operation), real_dispatch(operation, args))[1]
                def counted(*args):
                    selects[0] += 1
                    return real_select(*args)
                read_fd, write_fd = os.pipe()
                def later():
                    time.sleep(1)
                    events.append('sent')
                    os.write(write_fd, b'{"operation":"status","args":{}}\n{"operation":"_shutdown","args":{}}\n')
                    os.close(write_fd)
                threading.Thread(target=later, daemon=True).start()
                out = io.StringIO()
                select.select = counted
                try:
                    host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out)
                finally:
                    select.select = real_select
            finally:
                h.close()
        self.assertIn('result', json.loads(out.getvalue().splitlines()[0]))
        self.assertLessEqual(selects[0], 3, f'{selects[0]} wakeups in an idle second (the old loop made about 45)')
        at = events.index('status')
        self.assertEqual(events[at - 1], 'tick', 'a request after a long wait is answered after a fresh round')
        self.assertLess(events.index('sent'), at - 1, 'and that round ran after the request came in')

    def test_a_reply_starts_with_its_request_id_and_a_request_without_one_gets_none(self):
        """Beta.18 (notes/STOPPING-explore-20261005.md section 3): the bridge stops waiting after 10 s and must drop the
        late reply rather than read it as the next request's answer. So each reply carries its request's id, first."""
        import io, json, os
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                read_fd, write_fd = os.pipe()
                for line in ({'id': 7, 'operation': 'status', 'args': {}}, {'id': 8, 'operation': 'no_such_operation', 'args': {}},
                             {'operation': 'status', 'args': {}}, {'id': True, 'operation': 'status', 'args': {}}):
                    os.write(write_fd, (json.dumps(line)+'\n').encode())
                os.write(write_fd, b'{"id":9,"operation":"_shutdown","args":{}}\n{"id":10,"operation":"status","args":{}}\n')
                os.close(write_fd)
                out = io.StringIO()
                host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out, heartbeat=0)
            finally:
                h.close()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 4, 'the _shutdown with an id ends the loop, and request 10 is never answered')
        self.assertTrue(lines[0].startswith('{"id": 7, "result": '), lines[0])
        self.assertEqual(json.loads(lines[1]), {'id': 8, 'error': 'Unsupported operation or fields'})
        self.assertEqual(list(json.loads(lines[2])), ['result'], 'no id in, no id out')
        self.assertEqual(list(json.loads(lines[3])), ['result'], 'only a whole number is an id')

    def test_the_heartbeat_names_a_slow_request_while_it_runs_and_every_line_stays_whole(self):
        """A slow request used to look like a dead host. The heartbeat keeps beating on its own thread while the loop is
        stuck in one request, naming it, and its lines never split a reply."""
        import io, json, os, threading, time
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            try:
                real_dispatch = h.dispatch
                def slow(operation, args):
                    if operation == 'task_detail':
                        time.sleep(1.2)  # a git call on a busy Mac
                        return {'big': 'x' * 200000}
                    return real_dispatch(operation, args)
                h.dispatch = slow
                read_fd, write_fd = os.pipe()
                os.write(write_fd, b'{"id":1,"operation":"task_detail","args":{}}\n{"id":2,"operation":"status","args":{}}\n')
                def later():
                    time.sleep(2)  # after both answers the loop waits for a request a while, beating as idle
                    os.write(write_fd, b'{"operation":"_shutdown","args":{}}\n')
                    os.close(write_fd)
                class Slow(io.StringIO):
                    """Writes in small pieces, so a beat could land inside a reply if the lock didn't hold it off."""
                    def write(self, text):
                        for at in range(0, len(text), 4096):
                            super().write(text[at:at+4096])
                            time.sleep(0)
                        return len(text)
                out = Slow()
                threading.Thread(target=later, daemon=True).start()
                host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out, heartbeat=.1)
            finally:
                h.close()
        lines = [json.loads(line) for line in out.getvalue().splitlines()]  # every line parses: nothing interleaved
        beats = [line['heartbeat'] for line in lines if 'heartbeat' in line]
        # The state spine's push lines (thread_states.py) share stdout too; they go to the window, never to a caller.
        replies = [line for line in lines if 'heartbeat' not in line and 'event' not in line]
        self.assertEqual([reply['id'] for reply in replies], [1, 2])
        self.assertEqual(len(replies[0]['result']['big']), 200000)
        on = [beat for beat in beats if beat['operation'] == 'task_detail']
        self.assertGreaterEqual(len(on), 8, 'about one beat every 0.1 s through the 1.2 s request')
        self.assertEqual(on[0]['id'], 1)
        self.assertLess(on[0]['seconds'], on[-1]['seconds'])
        self.assertGreater(on[-1]['seconds'], .9)
        self.assertTrue(all(abs(beat['started'] - on[0]['started']) < .01 for beat in on), 'one start time for one request')
        last_reply = max(at for at, line in enumerate(lines) if 'id' in line)
        self.assertIn({'operation': None}, [line.get('heartbeat') for line in lines[last_reply:]], 'waiting for a request beats as idle')

    def test_the_heartbeat_starts_with_the_first_request_that_has_an_id(self):
        """Only the bridge sends ids. The Windows host smoke, the load tools and local-ui.test.mjs read every line as a
        reply, so a client that sends none sees no heartbeat."""
        import io, json, os
        def served(requests):
            with tempfile.TemporaryDirectory() as root:
                h = host.Host(Path(root)/'data')
                try:
                    read_fd, write_fd = os.pipe()
                    os.write(write_fd, requests + b'{"operation":"_shutdown","args":{}}\n')
                    os.close(write_fd)
                    out = io.StringIO()
                    host.serve(h, stdin=os.fdopen(read_fd, 'rb'), stdout=out)
                finally:
                    h.close()
            return [json.loads(line) for line in out.getvalue().splitlines()]
        plain = served(b'{"operation":"status","args":{}}\n' * 2)
        self.assertEqual([list(line) for line in plain], [['result'], ['result']], 'no ids, no heartbeat')
        bridged = served(b'{"operation":"status","args":{}}\n{"id":1,"operation":"status","args":{}}\n')
        self.assertEqual(list(bridged[0]), ['result'])
        self.assertTrue(any('heartbeat' in line for line in bridged), 'the first beat goes out as the first request with an id starts')
        self.assertEqual([line['id'] for line in bridged if 'heartbeat' not in line and 'id' in line], [1])

    def test_the_heartbeat_stops_quietly_when_the_window_has_gone(self):
        import host_heartbeat, threading
        class Gone:
            def write(self, text): raise BrokenPipeError()
            def flush(self): pass
        beats = host_heartbeat.Heartbeat(Gone(), threading.Lock(), every=.05).start()
        beats._thread.join(timeout=2)
        self.assertFalse(beats._thread.is_alive(), 'a closed pipe ends the heartbeat thread, not the host')
        beats.stop()

    def test_the_coding_coordinator_is_built_at_start_only_for_a_store_that_has_run_tasks(self):
        """run-lifecycle.md, finding 2, and the fixtures that assume a fresh store has no coding tables. A store that
        never ran a task stays lazy (no coordinator, no coding tables) until the first coding op. Once the coding_tasks
        table exists, the next start builds the coordinator before anything is asked, so a run the last host left
        open is already settled when status is read."""
        import json
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            self.assertFalse(hasattr(h, 'coding'), 'a fresh store stays lazy')
            self.assertIsNone(h.store.db.execute("SELECT 1 FROM sqlite_master WHERE name='coding_tasks'").fetchone())
            self.assertEqual(h.dispatch('status', {})['lostRuns'], [])
            profile = h.dispatch('add_profile', {'provider':'codex','identity':'expected@example.test','category':'personal'})['id']
            project = h.add_picked_project(root, profile, 'unverified-model')['id']
            channel = h.dispatch('create_conversation', {'projectId':project,'name':'Work','private':False})['id']
            (Path(root)/'test_sample.py').write_text('import unittest\n')
            task = h.dispatch('task_create', {'projectId':project,'channelId':channel,'text':'Do it','files':['test_sample.py'],'testFile':'test_sample.py'})
            saved = json.loads(h.store.db.execute('SELECT document FROM coding_tasks WHERE id=?', (task['id'],)).fetchone()[0])
            saved.update(state='working', runs=[dict(id='run-1', state='running', session_id='s', prior_attempts=0)])
            with h.store.db:
                h.store.db.execute('UPDATE coding_tasks SET document=? WHERE id=?', (json.dumps(saved), task['id']))
            h.store.close()  # no close(): the host "dies" with the run open
            h = host.Host(Path(root)/'data')
            try:
                self.assertTrue(hasattr(h, 'coding'), 'a store with tasks is reconciled at start')
                self.assertEqual([r['taskId'] for r in h.dispatch('status', {})['lostRuns']], [task['id']])
                with h.store.db:
                    h.store.db.execute('DELETE FROM local_channels WHERE id=?', (channel,))
                unnamed = h.dispatch('status', {})['lostRuns']
                self.assertEqual((unnamed[0]['taskId'], unnamed[0]['name']), (task['id'], 'Unnamed conversation'))
                with h.store.db:
                    h.store.db.execute('INSERT INTO local_channels VALUES (?,?)', (channel, 'Work'))
                with h.store.db:
                    h.store.db.execute("UPDATE threads SET visibility='private',owner='another-user' WHERE id=?", (channel,))
                self.assertEqual(h.dispatch('status', {})['lostRuns'], [], 'another actor cannot see a private lost task')
                with h.store.db:
                    h.store.db.execute("UPDATE threads SET visibility='shared',owner='local-user' WHERE id=?", (channel,))
                h.dispatch('task_settle', dict(projectId=project, channelId=channel, taskId=task['id']))
                self.assertEqual(h.dispatch('status', {})['lostRuns'], [], 'settled runs disappear without a restart')
            finally:
                h.close()

    def test_persistent_bound_conversation(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(hasattr(host, 'Host'), 'Host boundary is missing')
            h = host.Host(Path(root)/'data')
            profile = h.dispatch('add_profile', {'provider':'codex','identity':'expected@example.test','category':'personal'})
            project = h.add_picked_project(root, profile['id'], 'unverified-model')
            channel = h.dispatch('create_conversation', {'projectId':project['id'],'name':'Engineering','private':False})
            args = {'projectId':project['id'],'channelId':channel['id'],'clientId':'retry-1','text':'Keep this draft','parentId':None}
            sent = h.dispatch('queue',args)
            self.assertEqual(h.dispatch('queue',args),sent)
            self.assertEqual(sent['state'],'queued')
            h.close()
            h = host.Host(Path(root)/'data')
            timeline = h.dispatch('history',{'projectId':project['id'],'channelId':channel['id']})
            self.assertEqual([m['content'] for m in timeline],['Keep this draft'])
            self.assertEqual(h.dispatch('binding',{'projectId':project['id'],'channelId':channel['id']})['identity'],'expected@example.test')
            self.assertFalse(h.dispatch('status',{})['liveEnabled'])
            h.close()

    def test_reply_cancellation_private_and_project_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            h = host.Host(Path(root)/'data')
            profile = h.dispatch('add_profile', {'provider':'claude','identity':'private@example.test','category':'personal'})
            p = h.add_picked_project(root,profile['id'],'not-verified')['id']
            other = h.add_picked_project(root,profile['id'],'not-verified')['id']
            c = h.dispatch('create_conversation', {'projectId':p,'name':'Personal','private':True})['id']
            args = {'projectId':p,'channelId':c,'clientId':'one','text':'Private root','parentId':None}
            first = h.dispatch('queue',args)['id']
            reply = h.dispatch('queue',{**args,'clientId':'two','text':'Private reply','parentId':first})['id']
            rows = h.dispatch('history',{'projectId':p,'channelId':c})
            self.assertIn(['e',first,'','root'],rows[1]['tags'])
            self.assertEqual(h.dispatch('shared_context',{'projectId':p}),[])
            with self.assertRaises(ValueError): h.dispatch('history',{'projectId':other,'channelId':c})
            with self.assertRaises(ValueError): h.dispatch('queue',{**args,'actor':'remote'})
            h.dispatch('cancel',{'projectId':p,'channelId':c,'messageId':reply})
            self.assertEqual(h.store.turn(reply)['state'],'cancelled')
            self.assertEqual(len(h.dispatch('history',{'projectId':p,'channelId':c})),2)
            h.close()
            h = host.Host(Path(root)/'data')
            self.assertEqual(h.store.turn(reply)['state'],'cancelled')
            self.assertEqual(h.dispatch('shared_context',{'projectId':p}),[])
            h.close()

if __name__ == '__main__': unittest.main()
