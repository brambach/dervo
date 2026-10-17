import scenario from "./scenario.json" with { type: "json" };
export { scenario };
export type Status =
  | "blocked"
  | "review"
  | "active"
  | "changes"
  | "finished"
  | "approved";
export type Task = Omit<(typeof scenario.tasks)[number], "status"> & {
  status: Status;
};
export type Decision = {
  id: string;
  taskId: string;
  action: string;
  content: string;
  channelName: string;
  parentEventId: string;
  pubkey: string;
  createdAt: number;
};
export type WorkflowState = {
  version: 1;
  tasks: Task[];
  decisions: Decision[];
};
export function initialState(): WorkflowState {
  return {
    version: 1,
    tasks: scenario.tasks.map((t) => ({ ...t, status: t.status as Status })),
    decisions: [],
  };
}
export type ActionInput = {
  taskId: string;
  action: "changes" | "resolve" | "approve";
  text: string;
  id: string;
  createdAt: number;
};
export function decide(
  state: WorkflowState,
  input: ActionInput,
): WorkflowState {
  if (state.decisions.some((d) => d.id === input.id)) return state;
  const task = state.tasks.find((t) => t.id === input.taskId);
  if (
    !task ||
    task.status !== (input.action === "resolve" ? "blocked" : "review")
  )
    throw new Error("This task is not awaiting that decision.");
  if (!input.text.trim())
    throw new Error("Write a reply before recording your decision.");
  const status: Status =
    input.action === "resolve"
      ? "active"
      : input.action === "approve"
        ? "approved"
        : "changes";
  const label =
    input.action === "resolve"
      ? "scope recorded, blocker resolved. No execution authorised"
      : input.action === "approve"
        ? "onboarding-flow.md v1 only approved. No access or execution authorised"
        : "changes requested";
  const decision: Decision = {
    id: input.id,
    taskId: task.id,
    action: input.action,
    content: `${task.id} | Local sample decision: ${label}. ${input.text.trim()}`,
    channelName: task.channelName,
    parentEventId: task.rootId,
    pubkey: scenario.bryce,
    createdAt: input.createdAt,
  };
  return {
    ...state,
    tasks: state.tasks.map((t) => (t.id === task.id ? { ...t, status } : t)),
    decisions: [...state.decisions, decision],
  };
}
export function sections(state: WorkflowState) {
  return [
    {
      title: "Needs you",
      tasks: state.tasks.filter((t) =>
        ["blocked", "review"].includes(t.status),
      ),
    },
    {
      title: "In progress",
      tasks: state.tasks.filter((t) =>
        ["active", "changes"].includes(t.status),
      ),
    },
    {
      title: "Recently finished",
      tasks: state.tasks.filter((t) =>
        ["finished", "approved"].includes(t.status),
      ),
    },
  ];
}
