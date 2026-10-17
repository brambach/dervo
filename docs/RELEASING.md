# Dervo release workflow

Updated 15 September 2026. This is the operator procedure for the current source. A built app, a verified native journey and permission to distribute are separate milestones.

## Current boundary

Prebeta.26 has bounded native evidence for real Claude and Codex image input, saved images and normal restart. Real coding and automatic review have earlier native evidence. The newer app icon and image-send notice fix still need a staged candidate and native verification. Final usability, account lifecycle, fresh-Mac installation and manual update acceptance remain open. No tester release has been approved for distribution.

Release records and evidence belong outside the source checkout. The authoritative MVP1 goal and detailed historical evidence are retained in the private project feedback folder. Earlier versions of this document describe historical builds and mustn't be used as current installation instructions.

## Check the model lists

Once per beta, while planning it and again before its candidate, run `pnpm check:models` in `frontend/` (#46, 3 Oct 2026). It reads only public pages, with no account and no model call: Codex's published catalog for the version in `runtime/codex.json` and for the newest Codex on npm, Anthropic's models overview and Claude Code's model configuration page. It compares them with what Dervo hardcodes in `frontend/src/local/models.ts`, `providerLabel.ts`, `homeLaunch.ts`, `backend/home_codex.py` and `backend/claude_version.py`, and says what's new, what's gone, which model has no line and which one needs a newer Codex than Dervo bundles. It exits 1 on anything that would leave a picker, a default or Home wrong today. Codex's picker reads the live catalog at runtime, so a Codex model with no line still shows; Claude Code has no list a program can read, so a new Claude model doesn't show until `models.ts` names it. `--codex-catalog <file>` adds a saved signed-in catalog, and `--save <folder>` keeps the pages it read as evidence. `src/local/modelCheck.test.mjs` runs the same comparisons on saved copies in every test run.

## Build one candidate

Use Project settings > Build and preview > Open previews, or the managed `backend/release_builder.py` entry point. Don't use a new full source/runtime/compiler copy for each check. Don't use `build.sh` as the retained-release workflow.

The managed builder:

- Takes a source snapshot and uses the installed frontend dependencies. This isn't a fully reproducible dependency archive.
- Reuses one locked compiler cache per source project under `~/Library/Caches/Dervo/previews/`. It doesn't import old compiler output from another path.
- Requires at least 6 GiB free on both the cache and release volumes before starting. This is a starting threshold, not a guarantee that a build fits.
- Refuses a ninth app bundle in the immediate release folder. Keep the running app, rollback app and versions still referenced or distributed. Don't bypass the cap with another output directory.
- Retains a unique release folder under `~/Dervo Releases/` with the app, `release.json`, compressed source snapshot and check logs. It cleans its temporary source after normal completion or failure.
- Runs the configured feature, frontend and native checks, stages the bundle, verifies its profile and public account configuration, then signs and checks it locally.

Configure installed Node and Rust locations through Advanced build tools if discovery needs help. The CLI takes `--source`, a new `--output`, a local `--config` JSON file and `--profile private-beta` or `--profile personal-experimental`. Keep machine-specific tool paths and public project configuration in local configuration. Never put credentials in that JSON, command arguments or the source tree.

A failed build stays failed until a new candidate passes. Inspect its logs and preserve any known test failures. A successful build doesn't mean its UI or provider flow has passed.

## Keep storage bounded

Protect the current app, verified rollback and any distributed or referenced release before preparing an exact cleanup list. Existing deletion approvals apply only to their named paths. Delete generated apps separately from source snapshots, logs and workspaces. Record actual available space before and after deletion; logical sizes don't establish recovered bytes on APFS.

The eight-app limit doesn't prune source archives, backups or all caches. Those still need an explicit retention policy and separate review. Don't remove a live compiler cache or lower the build free-space guard.

## Test before adopting

Open preview starts the candidate with its own `preview-data` workspace. Current previews persist backend conversations and conversation drafts, including normalized image attachments. Appearance preferences in the isolated WebView reset when it closes. Older previews can behave differently.

Use disposable projects for acceptance. Record the exact app path, release ID, native build number and workspace used. Test:

1. Fresh launch and missing-prerequisite recovery.
2. Dervo sign-in, approved access, sign-out and restart.
3. Project creation, provider connection and a real coding request with automatic review.
4. Task guide, correction, Stop, Catch up and returning to the conversation after restart.
5. Image input, saved-image opening, draft isolation and recovery.
6. Project and conversation organisation, archive, restore and confirmed deletion. Source folders and unrelated accounts must remain.
7. Typing, streaming, Markdown/code, scrolling, reduced motion and errors in the actual native window.

Record pass, fail or not run separately. A synthetic provider or browser fixture doesn't prove native provider behaviour.

## Adopt a local preview and recover

After trying a completed candidate, choose Use this version and review the confirmation. The native flow saves pending drafts, refuses active or unresolved work, quits the old host, backs up the stopped workspace and opens the verified target. It keeps the old app. A failed preparation mustn't silently select a new version.

After switching, verify the running release and confirm projects, conversations, knowledge, drafts and account connections. A source edit doesn't update an already running app.

Return to previous version restores the workspace saved before that update. It first preserves newer work in another backup and doesn't roll back project source files. This needs explicit user confirmation. Don't launch an older app against newer data and assume compatibility.

This local preview flow isn't proof of a tester downloading and installing an update. The downloaded-package adoption and recovery journey still needs native acceptance.

## Release profiles and native identity

Private-beta permits Claude and Codex. OpenRouter remains unavailable regardless of renderer settings or environment flags. Personal-experimental is a separate build profile in the same codebase. Its profile marker alone doesn't establish working OpenRouter execution.

The builder stages `runtime/dervo-release-profile.json` and validated public Supabase configuration. Packaging rejects known workspace/credential files and recognizable secret patterns. That scan is limited and doesn't prove arbitrary source text contains no personal information.

Every managed build records a numeric product version, a separate increasing native build number and the exact Dervo release ID. The staged Info.plist, runtime marker and release record must agree before signing. Allocation is local to the locked project cache. Multi-machine publishing needs a shared ordering policy before use.

## Package a manual beta

Use `backend/release_package.py` with Python 3.13, a completed private-beta release, a new output directory outside that release and reviewed plain-text tester instructions:

```sh
python3 -B backend/release_package.py --release '/absolute/path/0.1.0-prebeta.N' --output '/absolute/path/packages/0.1.0-prebeta.N-zip' --instructions '/absolute/path/Start-here.txt'
```

For a DMG, add `--format dmg` and use a different output directory. The packager checks identity, profile, public account configuration, executable hash and signature. It reads the archive back and compares every app file and permission. Its output includes `package.json` and `SHA256SUMS.txt`. It doesn't install, upload or approve distribution.

Tester archives contain only the app and Start here instructions. Keep source archives, build logs, preview-data, accounts, conversation stores and backups out of the download. An installer archive doesn't replace a workspace backup.

Instructions must name the exact version, supported Mac requirements, account approval process, separate provider setup, installation/update steps, known limitations and feedback route. Verify those steps against the final archive on a fresh user or Mac. Previous archive readback on the development Mac isn't clean-install evidence.

Current runtime limits:

- Python 3.13 framework is required. The app offers its official download page and retry if missing. No automatic installation occurs.
- Codex is bundled. Published Claude Code is a separate installed provider tool with its own supported sign-in.
- This candidate is Apple silicon. The declared binary floor is macOS 11.0; that isn't a claim that all features have been tested on macOS 11. Current native acceptance has used macOS 26.5.2.
- Builds are locally ad-hoc signed. Apple Developer signing/notarization, automatic updates and the App Store are deferred. Gatekeeper and fresh-Mac opening remain acceptance checks.

## Private update discovery

The `202609150001_dervo_beta_releases.sql` migration adds release metadata and membership rules only. Bryce approved applying it and deploying the download function on 15 September; both are deployed in the configured beta project, with read-only schema and unauthenticated-denial checks recorded. It doesn't create an installer bucket, insert a release or approve a tester. Supabase Free's file limit is too small for the current installer. The prepared download route uses a private Cloudflare R2 bucket while Supabase keeps identity and release metadata. Bryce reports creating the private R2 bucket and saving the four server configuration entries. Their names were verified without exposing values. Actual R2 credential permissions and a real installer download remain unverified. Installer publication is pending authorization.

`backend/beta_updates.py` checks fresh membership and reads the newest numeric build. An explicit download request pins the published release and checksum, then calls `dervo-beta-download`. The server verifies the user's token with Supabase Auth, checks current beta access, reads the exact release through row-level security, checks the R2 object's size and recorded checksum, and reads the release again before signing. It uses a bucket-scoped read-only R2 credential. No service-role key or desktop storage credential is used.

Unpublished, future and withdrawn releases aren't available. Pending, expired and revoked members can't discover or request installers. Desktop accounts can't publish metadata or change installer files. The object metadata check doesn't calculate its contents' checksum, so publication still requires downloading and hashing the actual archive.

A download link lasts 120 seconds. It's a bearer link during that window, so withdrawing a release or revoking membership doesn't invalidate a link already issued. Don't put signed URLs in renderer state, logs, release records or screenshots. The host consumes the link in its download worker. The native picker supplies the destination folder; the renderer never receives a signed link. The worker checks exact byte count and SHA-256, then rechecks remote membership and release metadata. The main thread rechecks the account activation before saving the final filename. Existing files are never overwritten. Cancellation removes the temporary filename while allowing the worker to close its own descriptor. Downloading never launches or installs the package. No account or provider token goes in the browser URL. The signature contains a storage access-key identifier, but never its secret signing key.

Host/UI download wiring is implemented in source and awaits native rendering/interaction verification. Native manual adoption/recovery and authenticated hosted download checks remain pending. Source tests don't establish download readiness. Follow [private download setup](supabase/functions/SETUP.md) for exact server prerequisites and approval boundaries. Don't activate R2 or change billing automatically.

Before distribution, verify approved discovery/download, pending/revoked denial, withdrawal, archive integrity and the actual installer journey through the hosted service. Obtain explicit approval for the exact archive and release publication.

### Admin previews

Bryce, 24 September: a preview build goes to admins through Settings > Updates, and testers see only numbered betas. `supabase/migrations/202609240001_dervo_admin_previews.sql` adds an `admin-preview` channel and `public.dervo_is_admin()`. The read policy lets an approved, unexpired member read their own channel, and an admin also read `admin-preview`. `backend/beta_updates.py` asks for both channels and gets what the policy allows, so the database, not the app, decides who's offered a preview. `supabase/tests/releases.sql` checks that testers never see a preview, an admin does, and a lapsed admin sees nothing.

To publish a preview there, use the same package, release-sign, upload and catalog steps as a beta, with `channel='admin-preview'` in the catalog row. A preview still has to be notarised and release-signed, because the installer checks both. Its build number comes from the builder's counter, so the next beta is newer than any earlier preview and admins move onto it.

Order the first time: apply the migration (a hosted change, needs Bryce's go), then get an app that asks for both channels onto Bryce's Mac. Beta.14 only asks for `private-beta`, so it won't see the first preview. After that, every preview arrives in Settings > Updates.

Protocol references: [R2 signed URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [aws4fetch on R2](https://developers.cloudflare.com/r2/examples/aws/aws4fetch/), [Supabase function authentication](https://supabase.com/docs/guides/functions/auth), [Storage file limits](https://supabase.com/docs/guides/storage/uploads/file-limits).

## Release signing

A downloaded update installs only when the Dervo release key signed it. The key is one Ed25519 pair: the private half lives in 1Password as "Dervo release signing key" in Bryce's Personal vault and is never written to disk, the repo, a log or argv. The public half is `src/release-signing-key.hex`, compiled into the app by `src/release_signature.rs`. While that file is empty, every downloaded update is refused.

The signed message is the release id, build number, product version and installer SHA-256 (`Release::message`), so a signature can't be moved to another release or an older build. Before a download is saved or unpacked, the host runs this app's own executable as `dervo-local --verify-release-signature` (`backend/release_signature.py`). No signature, a signature over different values, or one from another key refuses the update with "This update isn't signed by Dervo. It wasn't installed." The SHA-256, size and bundle checks stay as they were. Local builds beside the app aren't downloads and aren't signature-checked. The first build that ships a key has to be installed by hand.

Make the key once. 1Password generates it, so it never exists outside 1Password until a signing read:

```sh
op item create --category "SSH Key" --ssh-generate-key ed25519 --title "Dervo release signing key" --vault Personal > /dev/null
cargo run --manifest-path tools/release-sign/Cargo.toml -- public-key > src/release-signing-key.hex
```

Back the item up offline too. If the key is lost, every installed app has to be updated by hand once to trust a new one.

Sign each packaged release after `release_package.py`, as its own step. `op read` asks for Touch ID; the key arrives over a pipe, stays in memory that's wiped on drop, and the tool refuses a key that doesn't match the compiled-in one:

```sh
cargo run --manifest-path tools/release-sign/Cargo.toml -- sign '/absolute/path/packages/0.1.0-beta.N-zip'
```

It rehashes the installer against `package.json` and writes `release-signature.json` beside it. Put its `signature` in the release's `dervo_beta_releases.signature` when publishing (`supabase/migrations/202609190003_dervo_release_signatures.sql`). Apply that migration before shipping a build that reads the column: an app that selects `signature` from a project without it can't check for updates.

## Ship a release to every platform

`scripts/ship-release.py` takes one Mac release to Windows and Linux too, then to everyone who hears about releases. Bryce runs it on his Mac; nothing in CI calls it, and `publish` and `announce` only print their plan until `--go` is given.

```sh
python3 scripts/ship-release.py prepare 0.1.0-beta.19          # writes .github/windows-release.json and linux-release.json
git commit -am 'Release 0.1.0-beta.19' && git push              # the Windows and Linux workflows build release packages
python3 scripts/ship-release.py collect 0.1.0-beta.19 --mac '/abs/packages/0.1.0-beta.19-dmg' --out '/abs/ship/0.1.0-beta.19'
python3 scripts/ship-release.py sign '/abs/ship/0.1.0-beta.19'  # Touch ID once per package
python3 scripts/ship-release.py publish '/abs/ship/0.1.0-beta.19' --notes notes.md [--channel admin-preview] [--go]
python3 scripts/ship-release.py announce '/abs/ship/0.1.0-beta.19' --notes notes.md [--go]
```

Each platform is its own catalog row with its own build number: `BASE` on the Mac, `BASE-windows`, and `BASE-linux-deb`, `BASE-linux-rpm` and `BASE-linux-appimage` (`supabase/migrations/202610080001_dervo_linux_releases.sql`). A Linux app asks only for its own format: an AppImage for AppImages, a .deb where dpkg installed Dervo, otherwise the .rpm (`beta_updates.linux_format`). `collect` refuses a package whose id, stamped app, checksum, product version or build number doesn't fit its row. `publish` refuses unsigned packages, uploads every installer to the app's R2 bucket first and then adds all the rows in one transaction, so no app sees a release whose installer isn't there. `announce` puts the installers and `beta/latest.json` in the website's bucket, makes a GitHub release in brambach/dervo with the installers attached, adds the release to CHANGELOG.md, and posts to Discord last. The Discord message mentions nobody.

The website's /beta section reads `beta/latest.json` in `dervo-beta-installers`:

```json
{"schema": 1, "releaseId": "0.1.0-beta.19", "version": "0.1.0", "title": "...", "notes": "...", "publishedAt": "2026-10-09T09:00:00+00:00",
 "downloads": [{"platform": "linux-x64", "format": "deb", "label": "Linux (.deb: Ubuntu, Debian)", "file": "0.1.0-beta.19/Dervo-linux-x64.deb", "bytes": 1, "sha256": "..."}]}
```

What it needs, set in the shell that runs it and never in the repo:

- `DERVO_R2_ACCOUNT_ID`: the Cloudflare account id, for the catalog's download origin.
- `DERVO_R2_BUCKET`: the bucket the app downloads from (default `dervo-beta-releases`).
- `DERVO_CATALOG_DATABASE_URL`: a Postgres connection string for the beta Supabase project that may insert into `dervo_beta_releases`.
- `DERVO_DISCORD_RELEASES_WEBHOOK`: a webhook for the Discord releases channel (Server Settings > Integrations > Webhooks).
- `DERVO_SITE_BUCKET`: the bucket trydervo.com serves /beta from (default `dervo-beta-installers`).
- `wrangler login` and `gh auth login` on the Mac.

Before the first run: apply `202610080001_dervo_linux_releases.sql` (a hosted change, Bryce's go), and the website's /beta needs to read the manifest. The Linux in-app installer isn't built yet, so Linux testers download updates from /beta or Settings > Updates and install them by hand.

### Code signing on Windows and Linux

Windows and Linux builds are unsigned today. Dervo's own release signature still protects in-app updates on both. What signing would need:

- Windows: an Authenticode certificate. Azure Trusted Signing (about $10 a month at the time of writing, after identity validation) or an OV/EV certificate on a hardware token or cloud HSM. Without it SmartScreen warns on first run. Tauri signs with `bundle.windows.signCommand`.
- Linux .deb and .rpm: a GPG key to sign packages, and an apt and dnf repository if updates should come through the system's package manager.
- AppImage: optional; a GPG signature embedded with `--sign`. Most AppImage users don't check it.
- macOS Intel: a second Mac build (`x86_64-apple-darwin`) signed and notarised with the existing Developer ID, and a `macos-x64` platform in the catalog.

## Error reports

With usage sharing on (the switch is now "Share usage and error reports with Dervo"), the app also reports errors by shape, never by text (`backend/error_reports.py`): the exception class, the file name, function and line of the innermost frame in Dervo's own backend files, the host operation or renderer screen, the provider for a coding task, the app version, the install id and a count. Sources are the host's request loop, the renderer's root error boundary (built-in error name and screen only) and coding tasks that fail inside Dervo rather than with a provider's error. Rows go to `dervo_error_reports` (`202609190004_dervo_error_reports.sql`), insert-only for testers, with server checks on every field. Admins read grouped counts on Testers, Errors, through `dervo_admin_errors()`. Installs that saw the earlier notice see the reworded one once.

## Release record

For each release retain its status, exact source hashes, profile, native identity, change list, known issues, test results and limits, archive checksum, native acceptance, backup/recovery evidence and distribution approval. Empty or missing evidence means not verified. Record public download and feedback destinations only after they're configured and approved.
