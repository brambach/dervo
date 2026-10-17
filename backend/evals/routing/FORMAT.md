# Routing eval case format

A suite is a JSON file holding a list of streams. A stream is a project's thread list at one moment, an optional
history of earlier Home messages, and a burst of new messages typed into Home, each with the route it should take.

```json
{
  "id": "dev-01",
  "about": "one line saying what this stream tests",
  "threads": [
    {"id": "t1", "name": "Sidebar scroll position", "state": "working",
     "request": "the first message that started the thread",
     "goal": "optional: the agent's one-line goal", "summary": "optional: the latest result line",
     "messages": ["optional: later user messages in that thread"],
     "pending_question": null, "archived": false, "pinned": false, "unread": false,
     "since_ago": 600, "at_ago": 30}
  ],
  "home": [{"text": "an earlier Home message", "ago": 90, "thread": "t1"}],
  "stream": [
    {"text": "also keep it when the window resizes", "gap": 4, "expect": "existing:t1"},
    {"text": "add csv export for invoices", "gap": 3, "expect": "new", "name_has": ["export"]},
    {"text": "make the csv one use semicolons", "gap": 5, "expect": "existing:@1"},
    {"text": "what's running right now?", "gap": 6, "expect": "home"},
    {"text": "fix the login thing and the export thing", "gap": 2, "expect": "unsure"},
    {"text": "the sort is off too", "gap": 20, "expect": "existing:t2|unsure"}
  ]
}
```

- `state` is the state spine's: `needs_you | working | quiet | unknown | stopped | finished | idle`.
- `since_ago` (seconds before the stream starts) is when the thread entered its state; `at_ago` its last activity.
- `gap` is seconds since the message before it (the first one: since the stream started). Default 5.
- `expect` is `existing:<thread id>`, `new`, `home` or `unsure`, or several joined by `|` when any of them is right.
  `@1`, `@2` name the first, second thread a `new` route made earlier in the same stream.
  `home` means Home answers itself: greetings, thanks, status questions. `unsure` means the rules should hand it to
  Home's model (several separate asks in one message, or truly ambiguous).
- `name_has` (on a `new` message, optional): words, any of which the new thread's name should contain (case-insensitive).

The scorer replays each stream in order: a message routed to a thread is added to that thread's messages and to Home's
history, and a `new` route adds a thread named by `thread_routing.name`, so later messages can follow it.
