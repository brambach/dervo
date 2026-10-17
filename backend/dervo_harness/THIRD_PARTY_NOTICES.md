# Third-party notices

`dervo_harness/upstream_adapted.py` contains rewrites of a small number of helpers from the
Hermes agent source snapshots stored read-only under `reference/`. Nothing else in this
package derives from upstream code. No upstream module is imported, vendored, or executed;
no Hermes installation, configuration, environment variable or home directory is read.

## Upstream licence

MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Adaptation map

The authoritative, machine-readable copy is `dervo_harness.ADAPTATION_MAP`.

| Dervo symbol | Upstream source | What changed |
|---|---|---|
| `parse_frontmatter` | `reference/agent_skill_utils.py.txt` `parse_frontmatter` | YAML loader removed; only the upstream `key: value` fallback path kept, plus `[a, b]` lists and quoted scalars. BOM strip and `---` fence detection retained. |
| `render_frontmatter` | none | New inverse of `parse_frontmatter`. |
| `truncate_description`, `SKILL_INDEX_DESC_LIMIT` | `extract_skill_description`, `SKILL_PROMPT_DESC_LIMIT` | Same 60-character index truncation, whitespace collapsed first. |
| `is_valid_skill_name` | `_NAMESPACE_RE`, `is_valid_namespace`; `reference/agent_learn_prompt.py.txt` naming rule | The prose authoring rule (lowercase, hyphens, max 64) as one regex. |
| `sanitize_context`, `build_context_block` | `reference/agent_memory_manager.py.txt` `sanitize_context`, `build_memory_context_block` | Fence tag renamed to `<dervo-context>`; note rewritten to say "reference data, not instructions"; no logger. |
| `strip_invisible_unicode` | `reference/agent_learn_prompt.py.txt` `_SOURCE_HYGIENE` (prompt prose) | The zero-width / bidi / tag-character rule implemented as code. |
| `iter_skill_markdown_files`, `EXCLUDED_SKILL_DIRS` | `iter_skill_index_files`, `EXCLUDED_SKILL_DIRS` | Org-mirror token gating and support-dir pruning removed; sorted walk that prunes VCS/dependency/cache dirs. |

## Dervo's own earlier sources kept as evidence

`reference/dervo_transport.py.txt`, `reference/dervo_claude_policy.py.txt`, `reference/dervo_claude_coding.py.txt`,
`reference/dervo_codex_policy.py.txt`, `reference/dervo_codex_adapter.py.txt`, `reference/dervo_codex_live.py.txt` and
`reference/dervo_codex_synthetic_peer.py.txt` are read-only snapshots of the native Dervo app's own backend
(`~/dervo/app/backend`: `transport.py`, `claude_policy.py`, `claude_coding.py`, `codex_policy.py`,
the `CodexAdapter` class of `adapters.py`, `codex_live.py`, and the `SyntheticPeer` double of `test_codex_live.py`),
copied on 2026-09-12 as the evidence behind `dervo_harness/claude_worker.py` and `dervo_harness/codex_worker.py`.
They are Dervo's code, not Hermes code, and the licence above does not apply to them. Nothing in them is imported
or executed by this package; the launch policy, protocol order and checks were re-implemented in the workers with
each source named in the module docstrings.

## Deliberately not adapted

- `agent_memory_manager.MemoryManager` (provider fan-out, threads, tool schema plumbing): Dervo has no provider plugins or tool registry in this slice.
- `agent_turn_context` (turn prologue, compaction, api_content sidecars): belongs to the execution loop, which is out of scope here.
- `agent_delegation_context` (context-var identity gates, kanban env scrubbing): Dervo carries authorization on every call as explicit `Caller` data instead of process-global or context-var state.
- Every config, `HERMES_HOME`, platform-detection and environment-detection helper in `agent_skill_utils`.
