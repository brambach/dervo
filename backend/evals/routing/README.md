# Routing evals

How smart Home is at putting messages in threads, naming them, ordering them and putting them away, as numbers that
can only go up. Everything here replays `thread_routing` (pure rules, no model, no host), so a full run takes about a
second and never touches an account.

    cd backend
    python3 -m evals.routing                    # the table, then every message routed wrong
    python3 -m unittest test_thread_routing     # the gate: fails if any suite scores below baseline.json
    python3 -m evals.routing.baseline           # after a change that scores better: raise the bar, same commit

## What's scored

Each suite (`streams_*.json`, format in FORMAT.md) is a list of streams: a project's threads at one moment, Home's
recent messages, and a burst of new messages, each with where a careful coordinator would send it.

| column | meaning |
|---|---|
| right | the route matched (kind and thread) |
| wrong | a misroute: the wrong thread, a thread started that shouldn't be, or Home answering work |
| model | `unsure`: the rules handed it to Home's model with the likeliest threads. Slower, not wrong |
| names | new threads whose name has one of the expected words |
| silent | routes confident enough (AUTO_ROUTE) to apply with no question |
| its acc | how many silent routes were right. This is the number that has to stay near 100% |
| sugg acc | routes applied with a Move offer (SUGGEST to AUTO_ROUTE) that were right |

The replay moves the world on as Home would: a routed message joins its thread, a `new` route adds a thread later
messages can follow, and a wrong route is moved where it belonged (as if you tapped Move), so one mistake is scored once.

Naming, sorting and cleanup cases are in `cases.py`.

## The suites

- `streams_dev.json`: tuned against. Several kinds of developers and styles.
- `streams_heldout.json`: written separately in Bryce's style; looked at once during tuning, so treat it as a second
  dev set.
- `streams_test.json`: written by a third writer who never saw the router or the other suites. Don't tune to it:
  read its score, not its failures. When it's been read too often, retire it into dev and have a new one written.
- `streams_learned.json` (not in git): your own corrections, from `learn.py`.

## Getting smarter from real use

The host logs each decision it applies and what happened next (format in `learn.py`). A Move after a route, a rename
after an auto-name and an unarchive after a tidy are the mistakes. `python3 -m evals.routing.learn --log <file>` turns
them into cases that replay with everything else. Fix the rule, check the gate, rewrite the case without anything
private, and move it into `streams_dev.json` so it guards every future change.
