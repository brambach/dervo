import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runTask} from './runtime.mjs';

const tool = {name: 'dervo_read_file', description: 'Read a host-approved file.',
  inputSchema: {type: 'object', properties: {path: {type: 'string'}}, required: ['path'], additionalProperties: false}};
const task = {runId: 'run-one', taskId: 'task-one', conversationId: 'conversation-one',
  model: 'openai/gpt-4.1-mini', prompt: 'Read README.md and summarize it.', systemPrompt: '', tools: [tool], maxModelCalls: 4};
const finish = {type: 'finish', reason: 'stop'};

test('actual Cline loop returns host tool results to the next model call', async () => {
  let calls = 0;
  const actions = [], output = [];
  const result = await runTask(task, {
    async *model(request) {
      assert.equal(request.model, task.model);
      assert.deepEqual(request.tools, [tool]);
      assert.equal('apiKey' in request, false);
      if (++calls === 1) {
        yield {type: 'tool-call-delta', index: 0, toolCallId: 'read-one', toolName: tool.name, input: {path: 'README.md'}};
        yield {type: 'finish', reason: 'tool-calls'};
      } else {
        assert.ok(request.messages.some(message => message.content.some(part => part.type === 'tool-result' && part.output === 'A project for field notes.')));
        yield {type: 'text-delta', text: 'This project stores field notes.'};
        yield finish;
      }
    },
    async tool(call) {
      actions.push(call);
      return {success: true, text: 'A project for field notes.'};
    },
  }, event => output.push(event));
  assert.equal(result.state, 'completed');
  assert.equal(calls, 2);
  assert.deepEqual(actions, [{name: tool.name, arguments: {path: 'README.md'}, toolCallId: 'read-one'}]);
  assert.equal(output[0].kind, 'delta');
  assert.ok(output.every(event => event.runId === task.runId));
});

for (const failure of ['network 503', 'context window exceeded', 'rate limit']) {
  test(`uncertain ${failure} stops before the SDK can retry`, async () => {
    let calls = 0;
    const result = await runTask(task, {
      async *model() { ++calls; throw new Error(failure); },
      async tool() { assert.fail('No tool after model failure'); },
    }, () => {});
    assert.equal(result.state, 'stopped');
    assert.equal(calls, 1);
  });
}

test('budget refusal on the second call stops without repeating the first tool', async () => {
  let calls = 0, writes = 0;
  const result = await runTask(task, {
    async *model() {
      if (++calls > 1) throw new Error('Budget is exhausted');
      yield {type: 'tool-call-delta', toolCallId: 'one', toolName: tool.name, input: {path: 'README.md'}};
      yield {type: 'finish', reason: 'tool-calls'};
    },
    async tool() { ++writes; return {success: true, text: 'Read completed.'}; },
  }, () => {});
  assert.equal(result.state, 'stopped');
  assert.equal(writes, 1);
  assert.equal(calls, 2);
});

test('stopping during a model request prevents its late tool call', async () => {
  const abort = new AbortController();
  const result = await runTask(task, {
    async *model() {
      abort.abort();
      yield {type: 'tool-call-delta', toolCallId: 'one', toolName: tool.name, input: {path: 'README.md'}};
      yield finish;
    },
    async tool() { assert.fail('Late tool was executed'); },
  }, () => {}, abort.signal);
  assert.equal(result.state, 'stopped');
});

test('host permission failure ends the run without another model call', async () => {
  let calls = 0;
  const result = await runTask(task, {
    async *model() {
      ++calls;
      yield {type: 'tool-call-delta', toolCallId: 'one', toolName: tool.name, input: {path: '../secret'}};
      yield {type: 'finish', reason: 'tool-calls'};
    },
    async tool() { throw new Error('Scope revoked'); },
  }, () => {});
  assert.equal(result.state, 'stopped');
  assert.equal(calls, 1);
});

for (const events of [
  [{type: 'tool-result', toolCallId: 'one', toolName: tool.name, output: 'pretend', execution: 'provider'}, finish],
  [{type: 'reasoning-delta', text: 'private'}, finish],
  [{type: 'text-delta', text: 'Partial output with no finish'}],
  [{type: 'finish', reason: 'error', error: 'sk-or-v1-secret'}],
  [finish, {type: 'text-delta', text: 'Late output'}],
]) {
  test(`invalid or incomplete stream stops: ${events[0].type}/${events.length}`, async () => {
    const output = [];
    const result = await runTask(task, {async *model() {yield* events;}, async tool() {assert.fail();}}, event => output.push(event));
    assert.equal(result.state, 'stopped');
    assert.equal(JSON.stringify(output).includes('sk-or-v1-secret'), false);
    assert.equal(JSON.stringify(output).includes('private'), false);
  });
}

test('rejects credentials or subscription provider configuration before any broker call', async () => {
  for (const value of [{...task, apiKey: 'secret'}, {...task, providerId: 'codex'}, {...task, model: 'gpt-6-astra'}]) {
    await assert.rejects(runTask(value, {model() {assert.fail();}}, () => {}));
  }
});

test('model call limit bounds a tool loop', async () => {
  let calls = 0;
  const result = await runTask({...task, maxModelCalls: 2}, {
    async *model() {
      ++calls;
      yield {type: 'tool-call-delta', toolCallId: `call-${calls}`, toolName: tool.name, input: {path: 'README.md'}};
      yield {type: 'finish', reason: 'tool-calls'};
    },
    async tool() {return {success: true, text: 'Read completed.'};},
  }, () => {});
  assert.equal(calls, 2);
  assert.equal(result.state, 'stopped');
});

test('indexed argument chunks produce exactly one complete host call', async () => {
  let calls = 0;
  const actions = [];
  const result = await runTask(task, {
    async *model() {
      if (++calls === 1) {
        yield {type: 'tool-call-delta', index: 0, toolCallId: 'read-one', toolName: tool.name, inputText: '{"path":'};
        yield {type: 'tool-call-delta', index: 0, inputText: '"README.md"}'};
        yield {type: 'finish', reason: 'tool-calls'};
      } else {yield {type: 'text-delta', text: 'Read completed.'}; yield finish;}
    },
    async tool(call) {actions.push(call); return {success: true, text: 'Read completed.'};},
  }, () => {});
  assert.equal(result.state, 'completed');
  assert.deepEqual(actions, [{name: tool.name, arguments: {path: 'README.md'}, toolCallId: 'read-one'}]);
});

test('malformed JSON and changed call identity stop before any tool', async () => {
  for (const last of [
    {type: 'tool-call-delta', index: 0, inputText: 'not json'},
    {type: 'tool-call-delta', index: 0, toolCallId: 'changed', inputText: '"README.md"}'},
    {type: 'tool-call-delta', index: 1, toolCallId: 'read-one', inputText: '"README.md"}'},
  ]) {
    let requests = 0;
    const result = await runTask(task, {
      async *model() {
        ++requests;
        yield {type: 'tool-call-delta', index: 0, toolCallId: 'read-one', toolName: tool.name, inputText: '{"path":'};
        yield last;
        yield {type: 'finish', reason: 'tool-calls'};
      },
      async tool() {assert.fail('Malformed call must not reach the host');},
    }, () => {});
    assert.equal(result.state, 'stopped');
    assert.equal(requests, 1);
  }
});
