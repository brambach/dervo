"""The voice loop with fake models: every rule between hearing you and Home getting a message (dervo_voice/loop.py).

Stdlib only, no audio models, so it runs in the gate everywhere. The same loop with the real models and recorded clips
is test_voice_recordings.py.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from array import array

from dervo_voice import intents
from dervo_voice.journal import Journal
from dervo_voice.loop import FRAME, FRAME_SECONDS, Settings, VoiceLoop, spoken, strip_wake

SPEECH = array('h', [4000, -4000] * (FRAME // 2))
QUIET = array('h', bytes(FRAME * 2))


class FakeVad:
    def speech(self, frame):
        return 1.0 if frame[0] else 0.0


class FakeAsr:
    """Returns the next scripted transcript for each utterance. A second look at the same, longer audio (the loop's
    early check on short utterances) gets the same text."""
    def __init__(self, *texts):
        self.texts = list(texts)
        self.heard = []
        self.current = None
        self.last = None  # the loop hands over the same buffer object for both looks at one utterance

    def transcribe(self, samples):
        if samples is not self.last:
            self.current = self.texts.pop(0) if self.texts else ''
            self.last = samples
        self.heard.append(len(samples))
        return self.current


class FakeWake:
    def __init__(self):
        self.armed = False
        self.resets = 0

    def heard(self, frame):
        if self.armed:
            self.armed = False
            return 'hey_dervo'
        return None

    def reset(self):
        self.resets += 1


class FakeTurn:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = 0

    def complete(self, samples):
        self.asked += 1
        return self.answers.pop(0) if self.answers else True


class FakeEngines:
    def __init__(self, asr, wake=None, turn=None):
        self.vad, self.asr, self.wake, self.turn, self.tts = FakeVad(), asr, wake, turn, object()


class FakeSpeaker:
    """Speaks for 1 s of loop time per call; ``stop`` cuts it off."""
    def __init__(self):
        self.said = []
        self.stops = 0
        self.until = 0.0
        self.loop = None

    def say(self, text, key=None):
        self.said.append(text)
        self.until = max(self.until, self.loop.now) + 1.0

    def speaking(self):
        return self.loop.now < self.until

    def stop(self):
        self.stops += 1
        self.until = 0.0


class Rig:
    def __init__(self, test, *texts, mode='push_to_talk', turn=None, journal=None, **settings):
        self.events = []
        self.asr = FakeAsr(*texts)
        self.wake = FakeWake() if mode in ('wake',) else None
        self.speaker = FakeSpeaker()
        counter = iter(range(1, 1000))
        self.loop = VoiceLoop(FakeEngines(self.asr, self.wake, turn), self.events.append,
                              journal or Journal(None), self.speaker, Settings(mode=mode, **settings),
                              ids=lambda: f'utt-{next(counter)}')
        self.speaker.loop = self.loop

    def frames(self, frame, seconds):
        for _ in range(round(seconds / FRAME_SECONDS)):
            self.loop.feed(frame)

    def talk(self, seconds=1.0, pause=2.5):
        self.frames(SPEECH, seconds)
        self.frames(QUIET, pause)

    def ptt(self, seconds=1.0):
        self.loop.command({'type': 'ptt_down'})
        self.frames(SPEECH, seconds)
        self.frames(QUIET, 0.2)
        self.loop.command({'type': 'ptt_up'})

    def of(self, kind):
        return [event for event in self.events if event['type'] == kind]


class PushToTalk(unittest.TestCase):
    def test_hotkey_utterance_waits_out_the_undo_window_then_is_sent(self):
        rig = Rig(self, 'Fix the export crash')
        rig.ptt()
        self.assertEqual([e['text'] for e in rig.of('utterance')], ['Fix the export crash'])
        self.assertEqual(rig.of('send'), [], 'nothing goes to Home inside the undo window')
        self.assertEqual(rig.speaker.said, ['Got it.'], 'the short ack is spoken at once, before routing')
        rig.frames(QUIET, 3.1)
        self.assertEqual([(e['id'], e['text']) for e in rig.of('send')], [('utt-1', 'Fix the export crash')])

    def test_speech_without_the_hotkey_is_ignored_in_push_to_talk(self):
        rig = Rig(self, 'should not be heard')
        rig.talk()
        self.assertEqual(rig.asr.heard, [])
        self.assertEqual(rig.loop.state, 'idle')

    def test_releasing_the_hotkey_ends_the_utterance_without_waiting_for_a_pause(self):
        rig = Rig(self, 'Short one')
        rig.loop.command({'type': 'ptt_down'})
        rig.frames(SPEECH, 0.5)
        rig.loop.command({'type': 'ptt_up'})
        self.assertEqual(len(rig.of('heard')), 1)

    def test_a_long_pause_while_the_hotkey_is_held_does_not_end_it(self):
        rig = Rig(self, 'one long thought')
        rig.loop.command({'type': 'ptt_down'})
        rig.frames(SPEECH, 0.5)
        rig.frames(QUIET, 3.0)
        rig.frames(SPEECH, 0.5)
        self.assertEqual(rig.of('heard'), [])
        rig.loop.command({'type': 'ptt_up'})
        self.assertEqual(len(rig.of('heard')), 1)

    def test_an_empty_transcript_sends_nothing(self):
        rig = Rig(self, '')
        rig.ptt()
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual(len(rig.of('heard_nothing')), 1)


class UndoAndCancel(unittest.TestCase):
    def test_undo_click_inside_the_window_drops_it(self):
        rig = Rig(self, 'Start a thread for dark mode')
        rig.ptt()
        rig.loop.command({'type': 'undo', 'id': 'utt-1'})
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['click'])

    def test_undo_after_it_was_sent_says_too_late(self):
        rig = Rig(self, 'Start a thread for dark mode')
        rig.ptt()
        rig.frames(QUIET, 4)
        rig.loop.command({'type': 'undo', 'id': 'utt-1'})
        self.assertEqual(len(rig.of('undo_too_late')), 1)

    def test_saying_never_mind_takes_back_the_last_utterance(self):
        rig = Rig(self, 'Clean up old branches', 'Never mind.', mode='conversation', undo_seconds=4.0)
        rig.talk(pause=2.1)
        self.assertEqual(rig.of('cancelled'), [])
        rig.talk(seconds=0.5, pause=2.5)
        self.assertEqual([e['id'] for e in rig.of('cancelled')], ['utt-1'])
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [], 'neither the request nor "never mind" reaches Home')
        self.assertEqual(rig.speaker.said[-1], 'Okay, dropped that.')

    def test_never_mind_with_nothing_to_take_back_is_not_sent(self):
        rig = Rig(self, 'Never mind', mode='conversation')
        rig.talk()
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual(len(rig.of('cancel_ignored')), 1)


class SpokenYes(unittest.TestCase):
    def test_outward_request_is_read_back_and_sent_only_after_yes(self):
        rig = Rig(self, 'Push the export branch', 'Yes.', mode='conversation')
        rig.talk()
        confirm = rig.of('confirm')
        self.assertEqual(len(confirm), 1)
        self.assertEqual(confirm[0]['what'], 'pushes code')
        self.assertIn('Say yes', rig.speaker.said[-1])
        self.assertEqual(rig.of('utterance'), [], 'an outward request gets no undo window: it waits for a yes')
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [], 'silence is not a yes')
        rig.talk(seconds=0.4)
        self.assertEqual([e['text'] for e in rig.of('send')], ['Push the export branch'])

    def test_a_bare_sure_is_not_enough_for_an_outward_request(self):
        rig = Rig(self, 'Push the export branch', 'Sure.', 'Yes.', mode='conversation')
        rig.talk()
        rig.talk(seconds=0.4)
        self.assertEqual(rig.of('send'), [], '"sure" alone does not push')
        self.assertEqual(len(rig.of('confirm_unclear')), 1)
        self.assertIn('Say yes', rig.speaker.said[-1])
        rig.talk(seconds=0.4)
        self.assertEqual([e['text'] for e in rig.of('send')], ['Push the export branch'])

    def test_no_drops_it(self):
        rig = Rig(self, 'Merge the auth branch into main', 'No.', mode='conversation')
        rig.talk()
        rig.talk(seconds=0.4)
        rig.frames(QUIET, 10)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['declined'])

    def test_no_answer_times_out_and_drops_it(self):
        rig = Rig(self, 'Deploy the site', mode='conversation')
        rig.talk()
        rig.frames(QUIET, 9)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['timeout'])
        self.assertIn("didn't hear a yes", rig.speaker.said[-1])

    def test_a_new_request_instead_of_an_answer_drops_the_outward_one_and_goes_ahead(self):
        rig = Rig(self, 'Push the export branch', 'What is the login thread doing', mode='conversation')
        rig.talk()
        rig.talk()
        rig.frames(QUIET, 4)
        self.assertEqual([e['text'] for e in rig.of('send')], ['What is the login thread doing'])

    def test_yes_works_in_push_to_talk_without_holding_the_key(self):
        rig = Rig(self, 'Push the export branch', 'Yes')
        rig.ptt()
        self.assertEqual(rig.loop.state, 'listening')
        rig.talk(seconds=0.4)
        self.assertEqual([e['text'] for e in rig.of('send')], ['Push the export branch'])

    def test_clicking_confirm_on_screen_counts(self):
        rig = Rig(self, 'Push the export branch')
        rig.ptt()
        rig.loop.command({'type': 'confirm', 'id': 'utt-1', 'yes': True})
        self.assertEqual(len(rig.of('send')), 1)


class WakeWordAndFollowUp(unittest.TestCase):
    def test_wake_word_arms_it_and_a_follow_up_needs_no_wake_word(self):
        rig = Rig(self, 'Start a thread for dark mode', 'Also make the sidebar narrower', mode='wake')
        rig.talk()  # no wake word yet: ignored
        self.assertEqual(rig.asr.heard, [])
        rig.wake.armed = True
        rig.frames(QUIET, 0.1)
        self.assertEqual(len(rig.of('wake')), 1)
        rig.talk()
        rig.talk()  # inside the follow-up window
        rig.frames(QUIET, 4)
        self.assertEqual([e['text'] for e in rig.of('send')],
                         ['Start a thread for dark mode', 'Also make the sidebar narrower'])

    def test_listening_times_out_back_to_idle(self):
        rig = Rig(self, 'late', mode='wake')
        rig.wake.armed = True
        rig.frames(QUIET, 7.5)  # 2 s for a request running on from the wake phrase, then 5 s waiting for one
        self.assertEqual(rig.loop.state, 'idle')
        rig.talk()
        self.assertEqual(rig.asr.heard, [])

    def test_wake_phrase_is_stripped_from_the_transcript(self):
        self.assertEqual(strip_wake("Hey Dervo, what's the export thread doing?"), "What's the export thread doing?")
        self.assertEqual(strip_wake('Dervo, run the tests'), 'Run the tests', 'a cut-off wake phrase')
        self.assertEqual(strip_wake('Derivatives are fine'), 'Derivatives are fine')
        self.assertEqual(strip_wake('OK Dervo. Run the tests'), 'Run the tests')
        self.assertEqual(strip_wake('Run the tests'), 'Run the tests')


class TurnTaking(unittest.TestCase):
    def test_turn_model_saying_not_finished_keeps_listening_through_a_pause(self):
        turn = FakeTurn(False, True)
        rig = Rig(self, 'Add a button that exports the report as a PDF', mode='conversation', turn=turn)
        rig.frames(SPEECH, 0.6)
        rig.frames(QUIET, 1.0)   # thinking pause: the model says not done
        self.assertEqual(rig.of('heard'), [])
        rig.frames(SPEECH, 0.8)
        rig.frames(QUIET, 0.5)   # this time it says done, well before max_pause
        self.assertEqual(len(rig.of('heard')), 1)
        self.assertEqual(turn.asked, 2)

    def test_a_transcript_ending_on_a_dangling_word_waits_even_when_the_sound_says_done(self):
        rig = Rig(self, 'Add a button that', mode='conversation', turn=FakeTurn(True, True))
        rig.frames(SPEECH, 0.6)
        rig.frames(QUIET, 1.0)
        self.assertEqual(rig.of('heard'), [], '"that" is not the end of a request')
        rig.asr.texts.append('Add a button that exports the report as a PDF')
        rig.asr.last = None  # the next look is at the longer utterance
        rig.frames(SPEECH, 0.8)
        rig.frames(QUIET, 0.5)
        self.assertEqual([e['text'] for e in rig.of('heard')], ['Add a button that exports the report as a PDF'])

    def test_max_pause_ends_a_dangling_one_anyway(self):
        rig = Rig(self, 'Add a button that', mode='conversation', turn=FakeTurn(True, True, True))
        rig.talk(seconds=0.6, pause=2.2)
        self.assertEqual([e['text'] for e in rig.of('heard')], ['Add a button that'])

    def test_max_pause_ends_it_even_if_the_turn_model_keeps_saying_no(self):
        turn = FakeTurn(False, False, False)
        rig = Rig(self, 'trailing off', mode='conversation', turn=turn)
        rig.talk(seconds=0.6, pause=2.2)
        self.assertEqual(len(rig.of('heard')), 1)

    def test_a_click_is_not_an_utterance(self):
        rig = Rig(self, 'should not run', mode='conversation')
        rig.frames(SPEECH, FRAME_SECONDS)
        rig.frames(QUIET, 3)
        self.assertEqual(rig.asr.heard, [])


class BargeIn(unittest.TestCase):
    def test_talking_over_dervo_stops_its_voice_and_is_heard(self):
        rig = Rig(self, 'What about the login bug', mode='conversation')
        rig.loop.command({'type': 'speak', 'text': 'The export thread finished. Three tests passed.'})
        rig.speaker.until = rig.loop.now + 10
        rig.frames(QUIET, 0.5)
        rig.talk(seconds=0.8)
        self.assertEqual(len(rig.of('barge_in')), 1)
        self.assertGreaterEqual(rig.speaker.stops, 1)
        rig.frames(QUIET, 4)
        self.assertEqual([e['text'] for e in rig.of('send')], ['What about the login bug'])

    def test_hotkey_while_speaking_stops_the_voice(self):
        rig = Rig(self, 'Next')
        rig.loop.command({'type': 'speak', 'text': 'Long answer.'})
        rig.loop.command({'type': 'ptt_down'})
        self.assertEqual([e['source'] for e in rig.of('barge_in')], ['ptt'])

    def test_a_short_noise_while_speaking_does_not_stop_it(self):
        rig = Rig(self, 'x', mode='conversation')
        rig.loop.command({'type': 'speak', 'text': 'Long answer.'})
        rig.speaker.until = rig.loop.now + 10
        rig.frames(SPEECH, 0.1)
        rig.frames(QUIET, 0.5)
        self.assertEqual(rig.of('barge_in'), [])

    def test_talking_over_dervo_in_push_to_talk_silences_it_without_making_a_request(self):
        rig = Rig(self, 'should not become a request')
        rig.loop.command({'type': 'speak', 'text': 'The export thread finished.'})
        rig.speaker.until = rig.loop.now + 10
        rig.talk(seconds=0.8)
        self.assertEqual(len(rig.of('barge_in')), 1)
        rig.frames(QUIET, 4)
        self.assertEqual(rig.asr.heard, [], 'only the hotkey makes a request in push to talk')
        self.assertEqual(rig.of('send'), [])


class ReviewFixes(unittest.TestCase):
    def test_muting_takes_back_a_request_still_in_its_undo_window(self):
        rig = Rig(self, 'Fix the export crash')
        rig.ptt()
        rig.loop.command({'type': 'mute'})
        rig.frames(QUIET, 4)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['mute'])

    def test_muting_drops_an_outward_request_waiting_for_yes(self):
        rig = Rig(self, 'Push the export branch', mode='conversation')
        rig.talk()
        rig.loop.command({'type': 'mute'})
        rig.frames(QUIET, 10)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['mute'])

    def test_a_refused_send_is_not_replayed_later(self):
        journal = Journal(None)
        rig = Rig(self, 'Fix the export crash', journal=journal)
        rig.ptt()
        rig.frames(QUIET, 3.1)
        rig.loop.command({'type': 'failed', 'id': 'utt-1', 'detail': 'Signed out'})
        self.assertEqual(journal.state('utt-1'), 'cancelled')
        self.assertEqual([e['id'] for e in rig.of('send_failed')], ['utt-1'])


class MuteAndState(unittest.TestCase):
    def test_mute_ignores_everything_until_unmuted(self):
        rig = Rig(self, 'after', mode='conversation')
        rig.loop.command({'type': 'mute'})
        rig.loop.command({'type': 'ptt_down'})
        rig.talk()
        self.assertEqual(rig.asr.heard, [])
        rig.loop.command({'type': 'unmute'})
        rig.talk()
        self.assertEqual(len(rig.of('heard')), 1)

    def test_status_reports_engines_and_pending(self):
        rig = Rig(self, 'Fix it')
        rig.ptt()
        rig.loop.command({'type': 'status'})
        status = rig.of('status')[-1]
        self.assertEqual(status['pending'], [{'id': 'utt-1', 'text': 'Fix it'}])
        self.assertTrue(status['engines']['asr'])

    def test_unknown_command_is_refused(self):
        rig = Rig(self)
        with self.assertRaises(ValueError):
            rig.loop.command({'type': 'format_disk'})


class JournalAndReplay(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.path = os.path.join(self.folder, 'voice-journal.jsonl')

    def test_every_step_is_written_before_it_is_acted_on(self):
        rig = Rig(self, 'Fix it', journal=Journal(self.path))
        rig.ptt()
        self.assertEqual(Journal(self.path).state('utt-1'), 'pending')
        rig.frames(QUIET, 4)
        self.assertEqual(Journal(self.path).state('utt-1'), 'released')
        with open(self.path) as handle:
            lines = [json.loads(line) for line in handle]
        self.assertNotIn('audio', json.dumps(lines), 'transcripts only, never audio')
        rig.loop.command({'type': 'delivered', 'id': 'utt-1'})
        self.assertEqual(Journal(self.path).unfinished(), [], 'delivered: finished, and dropped from the file')

    def test_a_crash_after_release_sends_again_with_the_same_id_once_delivered_never_again(self):
        rig = Rig(self, 'Fix it', journal=Journal(self.path))
        rig.ptt()
        rig.frames(QUIET, 4)
        # crash: no delivered came back
        again = Rig(self, journal=Journal(self.path))
        again.loop.recover()
        self.assertEqual([(e['id'], e['replay']) for e in again.of('send')], [('utt-1', True)])
        again.loop.command({'type': 'delivered', 'id': 'utt-1'})
        third = Rig(self, journal=Journal(self.path))
        third.loop.recover()
        self.assertEqual(third.of('send'), [])

    def test_a_crash_inside_the_undo_window_shows_it_again(self):
        rig = Rig(self, 'Fix it', journal=Journal(self.path))
        rig.ptt()
        again = Rig(self, journal=Journal(self.path))
        again.loop.recover()
        self.assertEqual([e['id'] for e in again.of('utterance')], ['utt-1'])
        again.frames(QUIET, 4)
        self.assertEqual([e['id'] for e in again.of('send')], ['utt-1'])

    def test_an_outward_request_waiting_for_yes_is_dropped_on_restart(self):
        rig = Rig(self, 'Push the export branch', journal=Journal(self.path))
        rig.ptt()
        again = Rig(self, journal=Journal(self.path))
        again.loop.recover()
        self.assertEqual(again.of('send'), [])
        self.assertEqual([e['reason'] for e in again.of('cancelled')], ['restart'])

    def test_a_line_cut_off_mid_write_is_skipped(self):
        with open(self.path, 'w') as handle:
            handle.write(json.dumps({'id': 'a', 'state': 'released', 'text': 'x'}) + '\n{"id": "b", "sta')
        self.assertEqual([e['id'] for e in Journal(self.path).unfinished()], ['a'])

    def test_the_next_record_after_a_cut_off_line_is_not_lost(self):
        with open(self.path, 'w') as handle:
            handle.write(json.dumps({'id': 'a', 'state': 'released', 'text': 'x'}) + '\n{"id": "b", "sta')
        Journal(self.path).write('c', 'pending', text='y')
        self.assertEqual(sorted(e['id'] for e in Journal(self.path).unfinished()), ['a', 'c'])

    def test_a_cancelled_transcript_does_not_stay_on_disk(self):
        journal = Journal(self.path)
        journal.write('a', 'pending', text='the secret plan')
        journal.write('a', 'cancelled', reason='voice')
        with open(self.path) as handle:
            self.assertNotIn('secret', handle.read())

    def test_compaction_keeps_only_unfinished(self):
        journal = Journal(self.path)
        for n in range(500):
            journal.write(f'u{n}', 'pending', text='t')
            journal.write(f'u{n}', 'cancelled')
        journal.write('keep', 'released', text='t')
        self.assertEqual([e['id'] for e in Journal(self.path).unfinished()], ['keep'])
        with open(self.path) as handle:
            self.assertLess(sum(1 for _ in handle), 410)



class MicAndScope(unittest.TestCase):
    def mic_rig(self, *texts, **kwargs):
        rig = Rig(self, *texts, **kwargs)
        rig.mic = []
        rig.loop.capture = rig.mic.append
        rig.loop.mic()
        return rig

    def test_hold_to_talk_opens_the_mic_only_while_held(self):
        rig = self.mic_rig('Fix the export crash')
        self.assertEqual(rig.mic, [False], 'closed at start in hold to talk')
        rig.ptt()
        self.assertEqual(rig.mic, [False, True, False], 'open for the hold, closed once the words are in')

    def test_hold_to_talk_opens_the_mic_for_a_spoken_yes_and_closes_it_after(self):
        rig = self.mic_rig('Push the export branch')
        rig.ptt()
        self.assertEqual(rig.mic[-1], True, 'Dervo asked for a yes, so it listens for one')
        rig.frames(QUIET, 9)
        self.assertEqual(rig.mic[-1], False, 'no answer: the ask times out and the mic closes')

    def test_wake_and_open_mic_keep_it_open_and_muting_closes_it(self):
        rig = self.mic_rig(mode='conversation')
        self.assertEqual(rig.mic, [True])
        rig.loop.command({'type': 'mute'})
        self.assertEqual(rig.mic, [True, False])
        rig.loop.command({'type': 'unmute'})
        rig.loop.command({'type': 'mode', 'mode': 'push_to_talk'})
        self.assertEqual(rig.mic, [True, False, True, False])

    def test_each_utterance_carries_the_project_it_was_heard_in(self):
        rig = Rig(self, 'Fix the export crash')
        rig.loop.command({'type': 'scope', 'scope': 'project-a:work'})
        rig.ptt()
        rig.frames(QUIET, 3.1)
        self.assertEqual([e['scope'] for e in rig.of('send')], ['project-a:work'])

    def test_the_window_can_drop_a_released_request(self):
        journal = Journal(None)
        rig = Rig(self, 'Fix the export crash', journal=journal)
        rig.ptt()
        rig.frames(QUIET, 3.1)
        rig.loop.command({'type': 'drop', 'id': 'utt-1'})
        self.assertEqual(journal.state('utt-1'), 'cancelled')

    def test_old_unsent_words_are_not_replayed(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, 'voice-journal.jsonl')
        with open(path, 'w') as handle:
            handle.write(json.dumps({'id': 'old', 'state': 'released', 'text': 'x', 'at': time.time() - 3600}) + '\n')
        rig = Rig(self, journal=Journal(path))
        rig.loop.recover()
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['stale'])

    def test_a_hotkey_held_past_the_limit_ends_the_utterance_and_the_buffer_stays_bounded(self):
        rig = Rig(self, 'one very long thought', max_utterance=2.0)
        rig.loop.command({'type': 'ptt_down'})
        rig.frames(SPEECH, 5.0)
        self.assertEqual(len(rig.of('heard')), 1)
        self.assertLessEqual(len(rig.loop.buffer), 2.1 * 16000)

class LiveProcessWithoutAudio(unittest.TestCase):
    """The real process, with a helper that never sends a frame (mic denied, no input device)."""

    def test_deadlines_and_commands_still_work_and_closing_stdin_ends_it(self):
        folder = tempfile.mkdtemp()
        journal = os.path.join(folder, 'voice-journal.jsonl')
        with open(journal, 'w') as handle:
            handle.write(json.dumps({'id': 'u1', 'state': 'pending', 'text': 'Fix the export crash',
                                     'at': time.time()}) + '\n')
        helper = os.path.join(folder, 'silent-helper')
        with open(helper, 'w') as handle:
            handle.write('#!/bin/sh\ncat > /dev/null\n')  # reads playback, never writes a frame, exits with stdin
        os.chmod(helper, 0o755)
        process = subprocess.Popen([sys.executable, '-m', 'dervo_voice', '--models', folder, '--journal', journal,
                                    '--helper', helper, '--settings', json.dumps({'undo_seconds': 1.0})],
                                   cwd=os.path.dirname(os.path.abspath(__file__)), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        process.stdin.write(json.dumps({'type': 'status'}) + '\n')
        process.stdin.flush()
        # Read until the send arrives instead of sleeping a fixed time: start-up (importing the libraries) varies a lot
        # on a busy machine. Then close stdin, as the app does when it quits.
        events = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not any(event['type'] == 'send' for event in events):
            line = process.stdout.readline()
            if not line:
                break
            events.append(json.loads(line))
        process.stdin.close()
        out, _ = process.communicate(timeout=10)
        events += [json.loads(line) for line in out.splitlines()]
        kinds = [event['type'] for event in events]
        self.assertIn('status', kinds, 'commands are answered with no audio coming in')
        self.assertIn(('send', 'u1'), [(event['type'], event.get('id')) for event in events],
                      'the undo window runs out on wall time')
        self.assertEqual(process.returncode, 0, 'stdin closing (the app quit) ends the process cleanly')

    def test_missing_libraries_say_so_instead_of_crashing(self):
        folder = tempfile.mkdtemp()
        from dervo_voice import engines
        open(os.path.join(folder, engines.VAD_FILE), 'wb').close()
        site = os.path.join(folder, 'site', 'onnxruntime')
        os.makedirs(site)
        with open(os.path.join(site, '__init__.py'), 'w') as handle:
            handle.write("raise ImportError('not in this build', name='onnxruntime')\n")
        import importlib.util
        if importlib.util.find_spec('numpy') is None:
            # numpy is imported first; a test Python without it (Linux CI's) gets a stand-in, so the planted onnxruntime
            # failure is the one reported.
            os.makedirs(os.path.join(folder, 'site', 'numpy'))
            open(os.path.join(folder, 'site', 'numpy', '__init__.py'), 'w').close()
        done = subprocess.run([sys.executable, '-m', 'dervo_voice', '--models', folder, '--site', os.path.dirname(site)],
                              cwd=os.path.dirname(os.path.abspath(__file__)), input='', capture_output=True, text=True, timeout=30)
        events = [json.loads(line) for line in done.stdout.splitlines()]
        missing = [event for event in events if event['type'] == 'unavailable' and event['reason'] == 'libraries']
        self.assertEqual(len(missing), 1, done.stdout + done.stderr)
        self.assertIn('onnxruntime', missing[0]['detail'])
        self.assertEqual(done.returncode, 0)

class Intents(unittest.TestCase):
    def test_outward_requests_are_caught(self):
        for text in ('Push the export branch', 'merge it into main', 'Deploy the site', 'publish the release notes',
                     'Send a message to Lauren on Slack', 'delete the old worktrees', 'force push the fix',
                     'open a pull request for the auth fix', 'Revert the last commit',
                     # From the correctness review: these used to slip through.
                     'deploy it to production', 'publish it', 'email Sarah the report', 'ship the fix',
                     'upload the build', 'close the pull request', 'force the branch onto main',
                     'discard my changes', 'rm -rf the build folder', 'trash the old branch', 'overwrite the config',
                     'reset the branch', 'unpublish the package', 'archive the repo', 'make the repo public',
                     'approve the PR', 'cherry-pick that onto main'):
            self.assertIsNotNone(intents.outward(text), text)

    def test_ordinary_requests_are_not(self):
        for text in ("What's the export thread doing", 'Start a thread for dark mode', 'Fix the crash on export',
                     'How are the tests going', 'Also make the sidebar narrower', 'Rename the settings page'):
            self.assertIsNone(intents.outward(text), text)

    def test_yes_no_and_cancel(self):
        for text in ('Yes.', 'yeah', 'Yes please', 'okay go ahead', 'Uh, yes.', 'do it'):
            self.assertTrue(intents.is_yes(text), text)
        for text in ('No.', 'nope', "don't", 'no wait', 'not now', 'Yes, no, wait', 'yeah no', 'okay wait',
                     'okay stop', 'sure, not that one'):
            self.assertFalse(intents.is_yes(text), text)
        for text in ('No.', 'nope', 'hold on'):
            self.assertTrue(intents.is_no(text), text)
        for text in ('Never mind.', 'scratch that', 'Cancel that', 'forget it'):
            self.assertTrue(intents.is_cancel(text), text)
        for text in ('Cancel the export job and start over', 'never mind the tests, fix the build'):
            self.assertFalse(intents.is_cancel(text), text)


class NeverSendWhileTalking(unittest.TestCase):
    def test_the_undo_window_waits_while_you_are_mid_sentence(self):
        rig = Rig(self, 'Clean up old branches', 'Never mind', mode='conversation', undo_seconds=1.0)
        rig.talk(seconds=0.5, pause=2.1)
        rig.frames(QUIET, 0.5)
        rig.frames(SPEECH, 1.5)   # the window closes while you're talking
        self.assertEqual(rig.of('send'), [], 'not sent while you are mid-sentence')
        rig.frames(QUIET, 2.2)
        self.assertEqual(rig.of('send'), [])
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['voice'])

    def test_a_short_answer_ends_at_once_even_when_the_turn_model_hesitates(self):
        rig = Rig(self, 'Push the export branch', 'No', mode='conversation', turn=FakeTurn(True, False))
        rig.talk(seconds=1.0, pause=2.1)
        rig.frames(SPEECH, 0.3)
        rig.frames(QUIET, 0.4)
        self.assertEqual([e['reason'] for e in rig.of('cancelled')], ['declined'], 'well before max_pause')


class Spoken(unittest.TestCase):
    def test_long_replies_are_cut_for_speech_and_point_at_the_screen(self):
        text = ('**Started** `Export fix`. ' + 'It reads the invoices table and writes one CSV per month. ' * 6)
        said = spoken(text)
        self.assertTrue(said.startswith('Started Export fix.'))
        self.assertTrue(said.endswith('The rest is on screen.'))
        self.assertLess(len(said), 260)

    def test_short_replies_are_kept_whole(self):
        self.assertEqual(spoken('Sent to [Auth](#t1).'), 'Sent to Auth.')


if __name__ == '__main__':
    unittest.main()
