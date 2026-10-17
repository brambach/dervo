import test from "node:test";
import assert from "node:assert/strict";
import * as workflow from "./workflow.ts";
test("project priority separates agent delivery from human approval", () => {
  assert.equal(typeof workflow.initialState, "function");
  const state = workflow.initialState();
  assert.equal(state.tasks.find((t) => t.id === "ONB-12").status, "review");
  assert.deepEqual(
    workflow.sections(state).map((s) => s.title),
    ["Needs you", "In progress", "Recently finished"],
  );
  assert.deepEqual(
    workflow.sections(state)[0].tasks.map((t) => t.id),
    ["ONB-14", "ONB-12"],
  );
});
test("explicit scope resolution resumes only the blocker, keeping its original question", () => {
  const before = workflow.initialState();
  const input = {
    taskId: "ONB-14",
    action: "resolve",
    text: "Read-only first. Ask separately before edits.",
    id: "ed".repeat(32),
    createdAt: 1789037001,
  };
  const next = workflow.decide(before, input);
  assert.equal(next.tasks.find((t) => t.id === "ONB-14").status, "active");
  assert.equal(
    next.tasks.find((t) => t.id === "ONB-14").summary,
    before.tasks[0].summary,
  );
  assert.equal(next.decisions[0].parentEventId, "c4".repeat(32));
  assert.equal(next.tasks.find((t) => t.id === "ONB-12").status, "review");
  assert.deepEqual(workflow.decide(next, input), next);
  assert.throws(
    () => workflow.decide(before, { ...input, taskId: "ONB-12" }),
    /not awaiting/,
  );
});
test("approval is explicit and scoped to the draft, not access or execution", () => {
  const next = workflow.decide(workflow.initialState(), {
    taskId: "ONB-12",
    action: "approve",
    text: "Draft direction approved.",
    id: "aa".repeat(32),
    createdAt: 1789037002,
  });
  assert.equal(next.tasks.find((t) => t.id === "ONB-12").status, "approved");
  assert.match(next.decisions[0].content, /v1 only/);
  assert.equal(next.tasks.find((t) => t.id === "ONB-14").status, "blocked");
});
test("request changes creates a durable, exact-thread reply and never approves", () => {
  assert.equal(typeof workflow.decide, "function");
  const before = workflow.initialState();
  const next = workflow.decide(before, {
    taskId: "ONB-12",
    action: "changes",
    text: "Explain revocation before inspection.",
    id: "ef".repeat(32),
    createdAt: 1789037000,
  });
  assert.equal(next.tasks.find((t) => t.id === "ONB-12").status, "changes");
  assert.equal(before.tasks.find((t) => t.id === "ONB-12").status, "review");
  assert.equal(next.decisions[0].parentEventId, "c2".repeat(32));
  assert.equal(next.decisions[0].channelName, "design");
  assert.match(next.decisions[0].content, /ONB-12.*Explain revocation/);
  assert.deepEqual(JSON.parse(JSON.stringify(next)), next);
  assert.throws(
    () =>
      workflow.decide(before, {
        taskId: "ONB-12",
        action: "changes",
        text: "  ",
        id: "ef".repeat(32),
        createdAt: 1,
      }),
    /Write/,
  );
});
