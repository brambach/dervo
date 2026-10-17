-- Release signatures (PLAN-release-signing-and-error-reports.md, part 1).
-- Each release row can carry the release key's Ed25519 signature over its SHA-256, release id, build number and product
-- version, made on Bryce's Mac with tools/release-sign and a key that lives in 1Password. The app checks it against the
-- public key compiled into it (src/release_signature.rs) and refuses a row without one, so an unsigned or re-signed row
-- written by anyone with access to this project installs nothing.
-- The column is nullable so existing rows stay readable. It adds no write access: testers still only read published rows.
begin;

alter table public.dervo_beta_releases
  add column signature text check (signature ~ '^[0-9a-f]{128}$');

commit;
