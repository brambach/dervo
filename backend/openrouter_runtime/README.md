# Personal OpenRouter loop

This directory uses the published Apache-2.0 @cline/agents 0.0.83 package. It supplies the agent/tool loop only for Dervo's personal OpenRouter profile. Claude Code and Codex retain their existing provider-owned runtimes. Cline's source license is retained in CLINE-LICENSE. The lockfile records the complete registry dependency tree and integrity hashes. Dependency license files remain in their packages.

Dervo supplies a custom AgentModel, so the Cline provider gateway is never constructed. The child has no OpenRouter key, subscription credentials, built-in file tools, telemetry service or independent session store. Python launches it with Node permissions and a macOS network/write sandbox. The host receives every model/tool request and must revalidate identity, scope and spending immediately before dispatch. Do not treat a queued bridge request as approval.

The runtime aborts on uncertain model failures before Cline can retry them. Tool calls are assembled from indexed fragments before entering Cline, and host error results retain their error state. Requests and responses are correlated to one child/run and bounded in size. Ended children cannot return actionable requests. The host terminates the owned process group on loss or revocation.

## Local validation

Install once with npm ci --ignore-scripts --omit=optional --legacy-peer-deps. No account/key is needed. The current checked dependency tree occupies about162 MB and must not be duplicated per UI test or native build.

From this directory, run node --test --test-concurrency=1 runtime.test.mjs under the host's deny-network test sandbox. From backend, run python3 -B release_checks.py test_openrouter_loop test_openrouter_request test_openrouter_budget test_openrouter_receipts with the intended Node binary on PATH. Node26.8.1 was tested. The parent requires the --permission flag and does not fall back to an unrestricted child.

## Evidence boundary

The actual Cline package and isolated Node child were exercised with synthetic model events and disposable host-side file operations. The host HTTP streaming transport also has injected-peer checks and a combined Cline/read/answer trial with synthetic receipts. Current account/key/route checks at dispatch, an interruptible worker, production coding-task/UI integration, durable conversation continuation, paid execution and personal native packaging are still pending. Current prebeta.26 is unchanged. Don't advertise these source checks as working paid OpenRouter coding inside the app.
