import { AgentRuntime } from '@cline/agents';

const MAX_BYTES = 4 * 1024 * 1024;
const ID = /^[A-Za-z0-9_-]{1,100}$/;
const MODEL = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._:/-]+$/;
const TOOL = /^dervo_[a-z_]{1,60}$/;
const STOP_MESSAGE = 'OpenRouter task stopped. Check its recorded result before continuing.';

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function bounded(value) {
  if (Buffer.byteLength(JSON.stringify(value), 'utf8') > MAX_BYTES) throw new Error(STOP_MESSAGE);
  return value;
}

function text(value, max, empty = false) {
  return typeof value === 'string' && (empty || value.length > 0) && Buffer.byteLength(value) <= max;
}

export function validateTask(task) {
  const fields = ['runId', 'taskId', 'conversationId', 'model', 'systemPrompt', 'prompt', 'tools', 'maxModelCalls'];
  if (!object(task) || Object.keys(task).length !== fields.length || fields.some(key => !(key in task))) throw new Error(STOP_MESSAGE);
  bounded(task);
  if (![task.runId, task.taskId, task.conversationId].every(id => typeof id === 'string' && ID.test(id)) ||
      !text(task.model, 200) || !MODEL.test(task.model) ||
      !text(task.systemPrompt, 65536, true) || !text(task.prompt, 1024 * 1024) ||
      !Number.isInteger(task.maxModelCalls) || task.maxModelCalls < 1 || task.maxModelCalls > 64 ||
      !Array.isArray(task.tools) || task.tools.length > 16) throw new Error(STOP_MESSAGE);
  const names = new Set();
  for (const tool of task.tools) {
    if (!object(tool) || Object.keys(tool).sort().join(',') !== 'description,inputSchema,name' ||
        !TOOL.test(tool.name) || names.has(tool.name) || !text(tool.description, 4096) ||
        !object(tool.inputSchema) || tool.inputSchema.type !== 'object') throw new Error(STOP_MESSAGE);
    names.add(tool.name);
  }
  return structuredClone(task);
}

// The Python host supplies all model events. This module has no provider key,
// provider gateway, filesystem tools, persistent sessions or telemetry service.
export async function runTask(input, broker, emit, signal) {
  const task = validateTask(input);
  let runtime;
  let failed = false;
  let calls = 0;
  let sequence = 0;
  let receivedBytes = 0;
  const toolResults = new Map();
  const stop = () => {
    failed = true;
    runtime?.abort(STOP_MESSAGE);
  };
  const fail = () => { stop(); throw new Error(STOP_MESSAGE); };
  const model = {
    async *stream(request) {
      if (failed || signal?.aborted || ++calls > task.maxModelCalls) fail();
      let bytes = 0;
      let finished = false;
      const toolCalls = new Map();
      const toolIds = new Map();
      try {
        const payload = bounded({model: task.model, systemPrompt: request.systemPrompt ?? '',
          messages: request.messages, tools: request.tools});
        for await (const event of broker.model(payload, request.signal)) {
          if (failed || signal?.aborted || finished || !object(event)) fail();
          const eventBytes = Buffer.byteLength(JSON.stringify(event));
          bytes += eventBytes;
          receivedBytes += eventBytes;
          if (bytes > MAX_BYTES || receivedBytes > 2 * MAX_BYTES) fail();
          if (event.type === 'text-delta') {
            if (Object.keys(event).sort().join(',') !== 'text,type' || !text(event.text, MAX_BYTES, true)) fail();
          } else if (event.type === 'tool-call-delta') {
            const allowed = ['type', 'index', 'toolCallId', 'toolName', 'inputText', 'input'];
            if (Object.keys(event).some(key => !allowed.includes(key)) ||
                (event.index !== undefined && (!Number.isInteger(event.index) || event.index < 0 || event.index > 63)) ||
                (event.toolName !== undefined && !task.tools.some(tool => tool.name === event.toolName)) ||
                (event.toolCallId !== undefined && (!text(event.toolCallId, 100) || !ID.test(event.toolCallId)))) fail();
            const key = event.index !== undefined ? `index:${event.index}` : `id:${event.toolCallId}`;
            if (event.index === undefined && event.toolCallId === undefined) fail();
            const call = toolCalls.get(key) ?? {inputText: ''};
            if (event.toolCallId !== undefined) {
              if ((call.id && call.id !== event.toolCallId) || (toolIds.has(event.toolCallId) && toolIds.get(event.toolCallId) !== key)) fail();
              call.id = event.toolCallId;
              toolIds.set(call.id, key);
            }
            if (event.toolName !== undefined) {
              if (call.name && call.name !== event.toolName) fail();
              call.name = event.toolName;
            }
            if (event.inputText !== undefined) {
              if (!text(event.inputText, MAX_BYTES, true) || call.input !== undefined) fail();
              call.inputText += event.inputText;
            }
            if (event.input !== undefined) {
              if (!object(event.input) || call.input !== undefined || call.inputText) fail();
              call.input = event.input;
            }
            toolCalls.set(key, call);
            if (toolCalls.size > 64) fail();
            continue;
          } else if (event.type === 'finish') {
            if (Object.keys(event).sort().join(',') !== 'reason,type' || !['stop', 'tool-calls'].includes(event.reason)) fail();
            if ((event.reason === 'tool-calls') !== (toolCalls.size > 0)) fail();
            // Cline identifies fragments by tool ID. Assemble indexed provider
            // chunks first so later argument fragments can't become extra calls.
            const ready = [];
            for (const call of toolCalls.values()) {
              if (!call.id || !call.name) fail();
              const input = call.input ?? JSON.parse(call.inputText);
              if (!object(input)) fail();
              ready.push({type: 'tool-call-delta', toolCallId: call.id, toolName: call.name, input});
            }
            for (const call of ready) yield call;
            finished = true;
          } else {
            // Reject provider-side execution, reasoning and arbitrary metadata.
            // Cost receipts and raw provider failures stay with the host.
            fail();
          }
          yield event;
        }
        if (!finished) fail();
      } catch {
        // Abort first. Cline's default transient/overflow retries must never
        // reissue a paid request with an uncertain outcome.
        fail();
      }
    },
  };
  runtime = new AgentRuntime({
    model, agentId: task.runId, sessionId: task.taskId, conversationId: task.conversationId,
    systemPrompt: task.systemPrompt, maxIterations: task.maxModelCalls, toolExecution: 'sequential',
    tools: task.tools.map(tool => ({...tool, retryable: false, maxRetries: 0,
      async execute(args, context) {
        if (failed || signal?.aborted) fail();
        try {
          const result = await broker.tool(bounded({name: tool.name, arguments: args,
            toolCallId: context.toolCallId}), context.signal);
          if (failed || signal?.aborted || !object(result) ||
              Object.keys(result).sort().join(',') !== 'success,text' ||
              typeof result.success !== 'boolean' || !text(result.text, 131072, true)) fail();
          receivedBytes += Buffer.byteLength(result.text);
          if (receivedBytes > 2 * MAX_BYTES) fail();
          toolResults.set(context.toolCallId, result);
          return result.text;
        } catch { fail(); }
      },
    })),
    hooks: {afterTool({toolCall}) {
      const result = toolResults.get(toolCall.toolCallId);
      toolResults.delete(toolCall.toolCallId);
      if (result) return {result: {output: result.text, isError: !result.success}};
    }, onEvent(event) {
      if (event.type === 'assistant-text-delta') {
        emit({kind: 'delta', runId: task.runId, seq: ++sequence, iteration: event.iteration, text: event.text});
      }
    }},
  });
  signal?.addEventListener('abort', stop, {once: true});
  try {
    if (signal?.aborted) stop();
    if (failed) throw new Error(STOP_MESSAGE);
    const result = await runtime.run(task.prompt);
    const completed = !failed && !signal?.aborted && result.status === 'completed';
    const summary = {kind: 'terminal', runId: task.runId, seq: ++sequence,
      state: completed ? 'completed' : 'stopped', modelCalls: calls};
    emit(summary);
    return summary;
  } catch {
    const summary = {kind: 'terminal', runId: task.runId, seq: ++sequence, state: 'stopped', modelCalls: calls};
    emit(summary);
    return summary;
  } finally {
    signal?.removeEventListener('abort', stop);
  }
}
