import assert from 'node:assert/strict';
import {test} from 'node:test';
const {stateWord,checkedAgo,taskTitle,pollEvery} = await import('./cloudRuns.ts');

test('state words come from the host state, and a brought-back run says so', () => {
  assert.equal(stateWord({state:'unknown',broughtBack:null}), 'Unknown');
  assert.equal(stateWord({state:'ready',broughtBack:null}), 'Result ready');
  assert.equal(stateWord({state:'ready',broughtBack:'claude/fix'}), 'Brought back');
  assert.equal(stateWord({state:'failed',broughtBack:null}), 'Failed');
  assert.equal(stateWord({state:'ready',broughtBack:null,branch:null,candidates:['claude/a','claude/b']}), 'Pick a branch');
  assert.equal(stateWord({state:'ready',broughtBack:null,branch:'claude/a',candidates:[]}), 'Result ready');
});

test('checked time reads in plain words and is absent before the first check', () => {
  assert.equal(checkedAgo(null, 100), null);
  assert.equal(checkedAgo(100, 130), 'Checked just now');
  assert.equal(checkedAgo(100, 100 + 5 * 60), 'Checked 5 min ago');
  assert.equal(checkedAgo(100, 100 + 3 * 3600), 'Checked 3 h ago');
});

test('task title is the first non-empty line, cut at a limit', () => {
  assert.equal(taskTitle('\n  Fix the flaky test\nmore'), 'Fix the flaky test');
  assert.equal(taskTitle('x'.repeat(100), 10), 'xxxxxxxxx…');
});

test('the page only polls while a run is still open', () => {
  assert.equal(pollEvery([]), false);
  assert.equal(pollEvery([{state:'ready',checking:false}]), false);
  assert.equal(pollEvery([{state:'ready',checking:false},{state:'unknown',checking:false,following:true}]), 15000);
  assert.equal(pollEvery([{state:'unknown',checking:false,following:false}]), false);
  assert.equal(pollEvery([{state:'unknown',checking:false},{state:'starting',checking:false}]), 4000);
  assert.equal(pollEvery([{state:'ready',checking:true}]), 4000);
});
