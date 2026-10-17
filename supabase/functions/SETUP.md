# Private beta downloads

Status, 15 September 2026: Bryce reports creating the private R2 bucket and adding the four server entries. A read-only check confirmed their names without exposing values. He approved the exact release migration and function deployment; both succeeded and hosted schema/unauthenticated-denial checks passed. No installer is uploaded or published. Real R2 credential scope, authenticated downloads and native update acceptance remain unverified. The steps below also document setup for a future project.

## What Bryce needs to do

1. Open Cloudflare Dashboard and select the account that should own Dervo's installer storage. Open R2 Object Storage. If activation asks for billing information or a subscription, review and accept it yourself before continuing. Free allowances don't guarantee zero charges. Supabase can remain on Free.
2. In that account, create a Standard storage bucket named `dervo-beta-releases`. Keep Public Development URL (`r2.dev`) disabled and don't attach a public custom domain. Don't configure expiration or cleanup rules that remove retained releases.
3. Create an R2 S3 API credential with **Object Read only**, limited to this bucket. Use a dedicated credential for the download function. Don't grant account administration or write access. Keep the credential in your password manager and enter it directly in the Supabase project's Edge Function Secrets screen. Don't put it in chat, source, shell arguments, screenshots or an installer.
4. Add the entries in the following table to the confirmed Supabase beta project. Use the selected R2 account ID and this Supabase project's publishable key. Don't use a Supabase service-role or secret API key.

| Server entry | Value source |
| --- | --- |
| `SUPABASE_URL` | Supabase provides this automatically. |
| `DERVO_SUPABASE_PUBLISHABLE_KEY` | The beta project's public publishable key. |
| `DERVO_R2_ACCOUNT_ID` | Cloudflare account ID, not the bucket name. |
| `DERVO_R2_ACCESS_KEY_ID` | The bucket-scoped Object Read only credential. |
| `DERVO_R2_SECRET_ACCESS_KEY` | Its secret access key. Server secret storage only. |

These steps need Bryce's involvement. Stop before enabling services, changing billing or creating credentials on his behalf. Once setup is complete, start with read-only configuration and connection checks. Don't ask Bryce to paste any values into chat.

## Deployment boundary

The reviewed migration is `supabase/migrations/202609150001_dervo_beta_releases.sql`. It creates only release metadata and read permissions. It was applied to the configured beta project with explicit approval. Applying it to any other project still requires approval for that exact hosted change. Confirm the destination project with a read-only check first.

Deploy `dervo-beta-download` only after separate deployment authorization and confirmed server secret setup. Select the verified project reference explicitly. The local `project_id` in `supabase/config.toml` isn't proof of the hosted destination.

`verify_jwt = false` disables the function gateway's legacy JWT check. The handler still requires a bearer token and verifies it against `/auth/v1/user` on the fixed Supabase project for every request. It rejects anonymous or unconfirmed identities, then checks `dervo_access` and reads release metadata using the same user token and row-level security. Never remove those checks or replace them with locally decoded claims. No service-role key is involved.

The endpoint accepts only POST with JSON `releaseId` and `sha256`. It has a 15-second request timeout and bounded request/upstream bodies. It doesn't enable browser CORS or log request headers, credentials or signed links. Native clients don't require CORS. Platform logs mustn't be configured to capture authorization headers or response bodies.

## Publish one reviewed archive

Uploading a package and publishing its metadata require explicit distribution authorization. Prepare the exact package, instructions, checksum and notes for review first. Don't reuse or overwrite a published object name.

The key is `<release_id>/Dervo-macos-arm64.zip` or the matching `.dmg`, in the fixed private bucket. Upload with appropriate content type and S3 metadata `sha256` containing the lowercase SHA-256 from the verified package. Retrieve the object and compute its full checksum before publishing. A HEAD metadata match alone doesn't establish byte integrity.

Use a separate bucket-scoped publishing credential or the authenticated Cloudflare dashboard to upload. The Edge function's read credential can't upload or delete installers. Never package publishing credentials. Keep any publisher credential in the existing local secret store or password manager.

The release row's `download_origin` must exactly equal `https://<R2-account-id>.r2.cloudflarestorage.com` for the function's configured account. Pin the release ID, numeric build number, object key, size, checksum, notes and format. Leave `published_at` null until publication is authorized and readback succeeds. Withdraw releases through the reviewed admin procedure rather than overwriting their objects.

## Verification before use

Local checks: `deno task check` and `deno task test` from this directory; backend `test_beta_updates` and `test_supabase_client`; disposable PostgreSQL `supabase/tests/run_local.py`. The tests use synthetic identities and fake signing keys. They don't access hosted accounts or R2.

After authorized deployment, verify unauthenticated, pending, expired and revoked requests are denied. Check an approved user's exact published release, missing object, checksum/size mismatch and withdrawal. Ensure R2 receives only its signed storage request, never the user's Supabase token. Check the actual archive checksum after download and verify the final native installation/update journey.

Links last 120 seconds. They can be forwarded within that window and an already issued link isn't immediately revoked by membership changes. Dervo's installed account gate remains separate. An expired link should prompt a fresh explicit download request. Automatic retries mustn't hide access denial.

Pricing and protocol references: [R2 setup](https://developers.cloudflare.com/r2/get-started/), [R2 pricing](https://developers.cloudflare.com/r2/pricing/), [R2 credentials](https://developers.cloudflare.com/r2/api/tokens/), [R2 signed URLs](https://developers.cloudflare.com/r2/api/s3/presigned-urls/), [Supabase function authentication](https://supabase.com/docs/guides/functions/auth).
