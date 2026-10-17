import * as React from "react";
import {
  decide,
  initialState,
  scenario,
  type ActionInput,
  type WorkflowState,
} from "./workflow";
declare global {
  interface Window {
    __DERVO_WORKFLOW__?: boolean;
    __DERVO_FIXTURE_READY__?: Promise<unknown>;
  }
}
export function isDervoFixture() {
  return import.meta.env.MODE === "e2e" && window.__DERVO_WORKFLOW__ === true;
}
function readState(): WorkflowState {
  const raw = localStorage.getItem(scenario.storageKey);
  if (!raw) return initialState();
  const state = JSON.parse(raw) as WorkflowState;
  if (
    state.version !== 1 ||
    !Array.isArray(state.tasks) ||
    !Array.isArray(state.decisions)
  )
    throw new Error(
      "Saved sample state cannot be read. Keep this origin data for recovery.",
    );
  return state;
}
export function useWorkflow() {
  const [state, setState] = React.useState(readState);
  React.useEffect(() => {
    const refresh = () => setState(readState());
    window.addEventListener("dervo-workflow", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("dervo-workflow", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);
  const commit = async (input: ActionInput) => {
    await window.__DERVO_FIXTURE_READY__;
    const next = decide(readState(), input);
    // One snapshot owns both task state and its reply. A failed save changes neither.
    localStorage.setItem(scenario.storageKey, JSON.stringify(next));
    window.dispatchEvent(new Event("dervo-workflow"));
    const reply = next.decisions.find((d) => d.id === input.id);
    if (reply) window.__BUZZ_E2E_EMIT_MOCK_MESSAGE__?.(reply);
    await window.__BUZZ_E2E_QUERY_CLIENT__?.invalidateQueries({ queryKey: [] });
  };
  return { state, commit };
}
export const statusLabel = {
  blocked: "Blocked on Bryce",
  review: "Needs your review",
  active: "In progress",
  changes: "Changes requested",
  finished: "Scope recorded",
  approved: "Draft approved locally",
};
