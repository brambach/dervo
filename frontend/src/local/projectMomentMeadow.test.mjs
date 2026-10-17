// 21 Sep: Bryce opened the preview and the meadow photo was across the whole window on an everyday thread. The
// project-created moment put it there and nothing ever took it away: the moment state lived for the session, so the
// overview thread of any project made that day kept the brand backdrop long after the overview had landed. The rule
// under test is the one thing that decides whether the moment still carries the photo.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {momentShowsMeadow} from './ProjectMoment.tsx';

test('waiting for access: the photo stays, whatever the thread says', () => {
  assert.equal(momentShowsMeadow({state: 'waiting'}, null), true);
  assert.equal(momentShowsMeadow({state: 'waiting'}, false), true);
});

test('the overview being written: the photo stays until its run ends', () => {
  assert.equal(momentShowsMeadow({state: 'running'}, undefined), true, 'no activity row yet is the first moments of the run');
  assert.equal(momentShowsMeadow({state: 'running'}, null), true);
  assert.equal(momentShowsMeadow({state: 'running'}, true), true);
  assert.equal(momentShowsMeadow({state: 'running'}, false), false, 'the overview landed: an everyday thread on paper');
});

test('a failed moment shows an error, and the meadow never sits above an error', () => {
  assert.equal(momentShowsMeadow({state: 'failed'}, true), false);
  assert.equal(momentShowsMeadow({state: 'failed'}, null), false);
});
