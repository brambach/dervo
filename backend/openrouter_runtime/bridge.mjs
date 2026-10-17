import {writeSync} from 'node:fs';
import {runTask, validateTask} from './runtime.mjs';

// One task per child, one outstanding host operation, bounded JSON lines.
// The launcher denies network, filesystem writes and child process creation.
const LIMIT = 4 * 1024 * 1024;
const abort = new AbortController();
let task = null;
let pending = null;
let nextId = 0;
let closed = false;
let started = false;
let buffer = Buffer.alloc(0);

function send(message) {
  const line = JSON.stringify(message) + '\n';
  if (Buffer.byteLength(line) > LIMIT) throw new Error('Message limit');
  const data = Buffer.from(line);
  let offset = 0;
  while (offset < data.length) offset += writeSync(1, data, offset);
}

function shape(message, keys) {
  return message && typeof message === 'object' && !Array.isArray(message) &&
    Object.keys(message).sort().join(',') === keys.sort().join(',');
}

function stop() {
  if (closed) return;
  closed = true;
  abort.abort();
  pending?.reject(new Error('Host operation stopped'));
  pending = null;
  process.stdin.destroy();
}

function request(kind, payload, signal) {
  if (closed || pending || signal?.aborted) throw new Error('Host operation unavailable');
  const id = ++nextId;
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  pending = {id, kind, resolve, reject, queue: [], bytes: 0, wake: null, ended: false};
  const slot = pending;
  const timer = setTimeout(stop, kind === 'model' ? 120000 : 600000);
  const cancelled = () => stop();
  signal?.addEventListener('abort', cancelled, {once: true});
  const cleanup = () => {
    clearTimeout(timer);
    signal?.removeEventListener('abort', cancelled);
    if (pending === slot) pending = null;
  };
  send({kind, id, runId: task.runId, payload});
  if (kind === 'tool') return promise.finally(cleanup);
  // Model calls deliver events, not a materialized response. The terminal
  // promise wakes a waiting iterator on cancellation or host failure.
  let failed = false;
  promise.catch(() => {failed = true; slot.wake?.();});
  return (async function* () {
    try {
      while (true) {
        if (closed || failed || signal?.aborted) throw new Error('Model stream stopped');
        if (slot.queue.length) {
          yield slot.queue.shift();
        } else if (slot.ended) {
          return;
        } else {
          await new Promise(yes => { slot.wake = yes; });
          slot.wake = null;
        }
      }
    } finally { cleanup(); }
  })();
}

async function start(value) {
  task = validateTask(value);
  await runTask(task, {
    model: (payload, signal) => request('model', payload, signal),
    tool: (payload, signal) => request('tool', payload, signal),
  }, send, abort.signal);
  stop();
}

function receive(message) {
  if (!started) {
    if (!shape(message, ['op', 'task']) || message.op !== 'start') throw new Error('Start required');
    started = true;
    void start(message.task).catch(() => {
      try {send({kind: 'protocol-error'});} finally {stop();}
    });
    return;
  }
  if (shape(message, ['op', 'runId']) && message.op === 'stop' && message.runId === task?.runId) {
    stop();
    return;
  }
  if (!pending || message.runId !== task?.runId || message.id !== pending.id || pending.ended) throw new Error('Unexpected response');
  if (pending.kind === 'tool') {
    if (!shape(message, ['op', 'runId', 'id', 'result']) || message.op !== 'result') throw new Error('Tool result required');
    pending.ended = true;
    pending.resolve(message.result);
  } else if (shape(message, ['op', 'runId', 'id']) && message.op === 'end') {
    pending.ended = true;
    pending.resolve();
    pending.wake?.();
  } else if (shape(message, ['op', 'runId', 'id', 'event']) && message.op === 'event') {
    pending.bytes += Buffer.byteLength(JSON.stringify(message.event));
    if (pending.bytes > LIMIT) throw new Error('Model response limit');
    pending.queue.push(message.event);
    pending.wake?.();
  } else {
    throw new Error('Model event required');
  }
}

process.stdin.on('data', chunk => {
  if (closed) return;
  try {
    buffer = Buffer.concat([buffer, chunk]);
    let end;
    while ((end = buffer.indexOf(10)) !== -1) {
      if (end > LIMIT) throw new Error('Line limit');
      const line = new TextDecoder('utf-8', {fatal: true}).decode(buffer.subarray(0, end));
      buffer = buffer.subarray(end + 1);
      receive(JSON.parse(line));
      if (closed) return;
    }
    if (buffer.length > LIMIT) throw new Error('Line limit');
  } catch {
    try {send({kind: 'protocol-error'});} finally {stop();}
  }
});
process.stdin.on('end', stop);
process.stdin.on('error', stop);
process.stdout.on('error', stop);
send({kind: 'ready', protocol: 1, engine: '@cline/agents', version: '0.0.83'});
