# Dervo beta account setup

Use a separate Supabase beta project. Dervo accounts control beta access; Claude and Codex keep their own provider sign-ins. Projects and conversations remain in the local workspace.

## Public configuration and secrets

The release builder accepts a local `accountPublicConfig` file with only `project_ref`, `url` and `publishable_key`. It validates those fields and stages a required public configuration in the app. Use the publishable key, never a service-role or secret key.

Keep Supabase CLI authentication in its existing local secret store. Enter the mail provider's sending credential directly in Supabase's SMTP settings. Dervo stores user sessions in macOS Keychain. Don't put credentials or sign-in codes in source, build arguments, release notes, screenshots or tester packages.

## Schema and access

Review and explicitly authorize the account migration before applying it to the selected project. New users must start with pending membership. Signing up mustn't create an admin or approve beta access.

Owner approval, first-admin creation and outside-tester approval are separate actions. Match the exact confirmed user ID before an authorized change, then verify the membership and audit record independently. Keep row policies and email confirmation enabled.

## Sending email

Configure custom SMTP before inviting testers outside the Supabase project team. The sender email's domain must match both the verified sending domain and the API key's permitted domain. A key restricted to one subdomain doesn't authorize another subdomain.

For Resend, use `smtp.resend.com`, port `465`, username `resend`, and a sending-only key restricted to the chosen domain. Enter that key directly in Supabase. The saved SMTP password is write-only: its returned representation can't establish the original key's prefix or validity.

## Configure both sign-in templates

In Supabase Authentication > Email > Templates, set **both Confirm signup and Magic Link** to:

- Subject: `Your Dervo sign-in code`
- Body: the complete contents of [sign-in-code.html](templates/sign-in-code.html)

Both templates must contain `{{ .Token }}` and mustn't contain a confirmation-link button. New and unconfirmed users take the signup-confirmation path, even when Dervo requests an email OTP. Existing confirmed users use Magic Link. Configuring only Magic Link leaves first-time users with the wrong email.

Dervo's current flow verifies the email and code inside the app. It doesn't consume browser callbacks. Don't work around a template problem by disabling confirmation, adding wildcard redirect URLs or running a localhost callback server. Leave unrelated email templates unchanged.

## Required release evidence

Read-only checks come first: validate the selected public project, verified HTTPS connectivity, email confirmation, both templates, sender domain and hosted schema permissions. Return only approved status fields from diagnostic tools. Don't print raw configuration or Auth logs.

Then verify these separately in the actual packaged app:

1. A new user receives a code, enters it in Dervo and sees pending access.
2. The session survives a normal restart without another code.
3. After explicit owner approval, the open app detects approved access.
4. Revoked or expired membership blocks new agent work while saved work stays readable.
5. Sign-out remains effective after restart, and signing in again uses a fresh code.
6. A returning confirmed user receives the code template too.
7. Real user credentials can't read another user's rows or grant tester/admin access.

Record provider execution, clean installation, manual upgrade and distribution approval separately. Successful email delivery alone doesn't verify sign-in or beta readiness.

References: [Supabase email templates](https://supabase.com/docs/guides/auth/auth-email-templates), [Supabase passwordless email](https://supabase.com/docs/guides/auth/auth-email-passwordless), [Resend SMTP](https://resend.com/docs/send-with-smtp).
