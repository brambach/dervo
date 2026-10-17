import assert from 'node:assert/strict';
import {test,afterEach} from 'node:test';
import ptBR from './pt-BR.json' with {type:'json'};
const {hostMessage,hostFailure,knownCodes,isHostMessage}=await import('./hostMessages.ts');
const {useLanguageForTests}=await import('./index.ts');

/** The host's refusals in the window's language (beta.17, section 2 item 3): English shows exactly what the host said,
 * a known message shows its translation, a message with names in it keeps them, and anything unknown passes through. */
afterEach(()=>useLanguageForTests('en'));

const SAMPLES={
  find_task:'Open this thread’s Progress panel and find task 7f3e. Wait for this task to finish, or use Stop.',
  file_held:'src/app.ts already held Docs’s uncommitted change when this task started, and Dervo can’t separate the two. Commit Docs first, then this one. Nothing was committed.',
  unexpected:'Something went wrong in Dervo’s workspace (ref 4f2a9c). Try again, and send feedback if it repeats.',
  home_text_limit:'Home reads up to 8000 characters at once. Split it into two messages.',
};

test('in English every known message reads exactly as the host sent it, and an unknown one passes through', ()=>{
  for(const text of [...Object.values(SAMPLES),'This conversation uses another account.','Unknown project'])assert.equal(hostMessage(text),text);
  assert.equal(hostMessage('Something Dervo has never said.'),'Something Dervo has never said.');
  assert.equal(hostMessage('Something went wrong in Dervo’s workspace. Try again, and send feedback if it repeats.'),
    'Something went wrong in Dervo’s workspace. Try again, and send feedback if it repeats.','the reference is optional');
});

test('the bridge’s busy and stopped lines (beta.18) say what it was busy with in plain words, in English and Portuguese',()=>{
  const busy='Dervo is busy with task detail, so this wasn’t done. Try again in a moment.';
  const still='Dervo is still busy with background work. It may still finish, so check before you try it again.';
  const send='Dervo is still busy with orchestrator send. It may still finish, so check before you try it again.';
  const odd='Dervo is busy with some operation nobody named, so this wasn’t done. Try again in a moment.';
  const stopped='Dervo’s local store stopped. It starts again with your next step. Your draft is retained.';
  // The bridge names the operation (src/bridge.rs words()); the window says it plainly. An operation it doesn’t know stays as it came.
  assert.equal(hostMessage(busy),'Dervo is busy with this thread, so this wasn’t done. Try again in a moment.');
  assert.equal(hostMessage(send),'Dervo is still busy with your message. It may still finish, so check before you try it again.');
  assert.equal(hostMessage(still),still);
  assert.equal(hostMessage(odd),odd);
  assert.equal(hostMessage(stopped),stopped);
  useLanguageForTests('pt-BR');
  assert.equal(hostMessage(busy),'O Dervo está ocupado com esta conversa, então isto não foi feito. Tente de novo daqui a pouco.');
  assert.equal(hostMessage(send),'O Dervo ainda está ocupado com sua mensagem. Isso ainda pode terminar, então confira antes de tentar de novo.');
  assert.equal(hostMessage(still),'O Dervo ainda está ocupado com trabalho em segundo plano. Isso ainda pode terminar, então confira antes de tentar de novo.');
  assert.equal(hostMessage(odd),'O Dervo está ocupado com some operation nobody named, então isto não foi feito. Tente de novo daqui a pouco.');
  assert.notEqual(hostMessage(stopped),stopped);
});

test('codes are unique and stable, and a code finds its message even when the text is the English', ()=>{
  const codes=knownCodes();
  assert.equal(new Set(codes).size,codes.length);
  assert.ok(codes.includes('conversation_other_account'));
  assert.equal(hostMessage('This conversation uses another account.','conversation_other_account'),'This conversation uses another account.');
});

test('a host call’s rejection: a string is translated, an Error is left alone', ()=>{
  const error=new Error('boom');
  assert.equal(hostFailure(error),error);
  assert.equal(hostFailure('Unknown project'),'Unknown project');
});

test('in Portuguese a known message shows its translation with the names it carried', ()=>{
  useLanguageForTests('pt-BR');
  const own=ptBR['This conversation uses another account.'];
  if(own)assert.equal(hostMessage('This conversation uses another account.'),own);
  const held=ptBR['{path} already held {who}’s uncommitted change when this task started, and Dervo can’t separate the two. Commit {who} first, then this one. Nothing was committed.'];
  if(held){
    const shown=hostMessage(SAMPLES.file_held);
    assert.notEqual(shown,SAMPLES.file_held);
    assert.match(shown,/src\/app\.ts/);
    assert.match(shown,/Docs/);
  }
  assert.equal(hostMessage('Something Dervo has never said.'),'Something Dervo has never said.','unknown stays English');
});

test('isHostMessage knows a message with names in it, as sent and in the window’s language (polish pass 2)',()=>{
  const busy='Dervo is busy with orchestrator send, so this wasn’t done. Try again in a moment.';
  const still='Dervo is still busy with orchestrator send. It may still finish, so check before you try it again.';
  const held=SAMPLES.file_held;
  for(const language of ['en','pt-BR']){
    useLanguageForTests(language);
    // local() hands callers the text already in the window's language, wrapped in an Error or as the string itself.
    const shown=text=>hostMessage(text);
    assert.equal(isHostMessage(shown(still),'host_busy_still'),true,language+' still busy');
    assert.equal(isHostMessage(new Error(shown(still)),'host_busy_still'),true,language+' as an Error');
    assert.equal(isHostMessage(shown(busy),'host_busy_still'),false,language+' skipped is not still busy');
    assert.equal(isHostMessage(shown(busy),'host_busy_skipped'),true,language+' skipped');
    assert.equal(isHostMessage(shown(still),'host_busy_skipped'),false,language+' still busy is not skipped');
    assert.equal(isHostMessage(shown(held),'file_held'),true,language+' names with punctuation');
    const odd='Dervo is still busy with (a) [b] $1. It may still finish, so check before you try it again.';
    assert.equal(isHostMessage(odd,'host_busy_still'),true,language+' the English as sent, whatever the names hold');
    assert.equal(isHostMessage(shown(odd),'host_busy_still'),true,language+' the same in the window’s language: names with pattern characters');
    assert.equal(isHostMessage('Something Dervo has never said.','host_busy_still'),false);
    assert.equal(isHostMessage(still,'no_such_code'),false);
  }
  useLanguageForTests('en');
});
