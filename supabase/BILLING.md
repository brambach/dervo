# Paid plans: setup and boundaries

Status, 8 Oct 2026: built and tested offline on branch `claude/paid-plans-trial-jjnadw`. Nothing is applied to the hosted project, no Paddle account or product exists for Dervo yet, and nobody has been charged or emailed. Every step below needs Bryce's go.

Locked by Bryce, 8 Oct 2026: **Dervo Pro, $15.00 a month or $144.00 a year, with a 7-day free trial** (card required). Create exactly these prices in Paddle. The app shows them from `PRICES` in `frontend/src/local/DervoPlan.tsx` and the trial length from `billing_settings.trial_days`, so all three must agree.

## How it works

- **Access stays in `dervo_memberships`.** A started trial or a Paddle subscription approves the membership with an end date and `access_source = 'billing'`. So `dervo_access()`, release downloads, feedback, usage and error reports all work for paying users with no other change, and older apps keep working.
- **Admin choices win.** Any membership change billing didn't make is marked `'admin'`. Billing never shortens an admin's open-ended approval and never undoes a removal. Existing rows default to `'admin'`, so no current tester's access changes.
- **The trial (default, `trial_mode = 'card'`):** 7 days, card required, bought at checkout. Dervo has two extra Paddle prices that carry the trial. The app sends a salted hash of the Mac with every checkout. `dervo-billing` uses the trial prices only when the account has never had a trial or a subscription, and its email (Gmail dots and `+tags` folded) and that Mac haven't had a trial on another account. The first trialing webhook records the email and Mac. A trialing subscription counts only if it was allowed then, so a second trial bought some other way grants nothing.
- **The no-card trial (`trial_mode = 'app'`, off):** started from the app by `dervo_start_trial()`. One per account, one per Mac (a salted SHA-256 of the machine's hardware UUID) and one per email (Gmail dots and `+tags` folded, stored hashed). Turn it on only if you'd rather not ask for a card.
- **Paying:** the app asks `dervo-billing` for a Paddle checkout for its own account. Nobody with an open subscription (active, trialing, past due or paused) can check out again; they manage it in the portal. One checkout at a time per account (`dervo_private.checkouts`): a double click, or a second click before the webhook arrives, reopens the same Paddle transaction for a day. Choosing the other interval first cancels the unpaid one in Paddle; if it's already paid, nothing new is sold. Paddle sends signed events to `dervo-billing-webhook`, which passes them untouched to `dervo_billing_ingest()`. That function checks the signature against a Vault secret, records each event once, ignores stale ones and records a price it doesn't know as `unknown_price`, then updates the membership.
- **Prices change safely.** Every price ID ever set is kept in `billing_prices`, so subscribers on an old price keep renewing after you change prices.
- **Retries:** an event for an account or subscription Dervo doesn't know yet fails with an error, so Paddle retries it rather than Dervo dropping it.
- **Renewals and failures:** access runs to the end of the paid period plus 2 days' grace (`grace_seconds`) for a late webhook. A failing renewal (`past_due`) keeps access for the grace period from when it started failing. A scheduled cancellation ends at its date. An actual cancellation, a full refund or a chargeback ends access at once. Paddle keeps a refunded subscription active, so it stays canceled in Dervo (`refunded_at`) through later updates, until a billing period starts after the refund.
- **Sandbox and live never mix.** Every subscription row records its environment, and only rows from the current `billing_settings.environment` count.
- **No service-role key** anywhere. The webhook function holds no secret at all. The checkout function holds only the Paddle API key.

## Where the paywall is enforced, and what it would take to move it

Today it's enforced in the app's local Python host (`backend/supabase_account.py`, `require_access`). The server decides who has access, but agent work runs on the user's own Claude and Codex accounts on their own Mac, so the host is what refuses to start it. Someone who edits the shipped Python, or blocks Supabase and rolls their clock back to keep the 5-minute grant alive, can keep using Dervo without paying. That's accepted for now (8 Oct 2026, default pending Bryce's call): it takes deliberate effort, it's the same trade most local developer tools make, and Paddle and the server stay correct for everyone who doesn't.

What would actually move value server-side, roughly in order of effort:

1. **Signed releases and updates only for paying accounts.** `dervo-beta-download` already checks membership. Making update checks need a current plan means a patched copy stops getting new versions. Small: mostly policy, the check exists.
2. **Server-held features.** Anything that has to live on Dervo's servers anyway (cloud send, the iPhone relay, sync, voice if it's server-side) checks `dervo_access()` on every call. Medium: it comes with those features, which other threads own.
3. **A signed, short-lived entitlement** that the host verifies with a public key built into a signed, notarized app. It raises the bar from editing Python to re-signing the app, but doesn't close it. Medium, and only worth it once the host is no longer editable source.

None of these can fully stop a determined user on their own Mac.

## Sandbox setup (Bryce)

1. Create a Paddle **sandbox** account at sandbox-vendors.paddle.com. Don't create a live account yet.
2. Create one product, **Dervo Pro**, with four prices: **$15.00 monthly** and **$144.00 yearly** with no trial, and the same two with a **7-day trial, card required**. In Checkout settings, don't add a trial anywhere else.
3. Set a **default payment link** in Checkout settings. This needs a page on an approved domain that loads Paddle.js and opens checkout from the `_ptxn` query parameter. That page belongs to the "Refresh the landing page" thread (trydervo.com/checkout).
4. Create a **notification destination** for `https://<project-ref>.supabase.co/functions/v1/dervo-billing-webhook`, subscribed to the `subscription.*` and `adjustment.*` events. Copy its secret straight into the Supabase SQL editor:
   `select vault.create_secret('<secret>', 'dervo_paddle_webhook_secret');`
   Don't paste it into chat, source or a shell command.
5. Create a sandbox **API key** limited to transactions (write), customers (read) and customer portal sessions (write). Enter it in the project's Edge Function secrets as `DERVO_PADDLE_API_KEY`, and set `DERVO_PADDLE_ENVIRONMENT` to `sandbox`. `DERVO_SUPABASE_PUBLISHABLE_KEY` is already there for the download function.
6. With an approved migration (`202610080001_dervo_billing.sql`) and deployment, set the price IDs:
   `update dervo_private.billing_settings set month_price_id = 'pri_…', year_price_id = 'pri_…', month_trial_price_id = 'pri_…', year_trial_price_id = 'pri_…';`
7. To use a 3-day trial instead, change the two trial prices in Paddle and set `trial_days = 3` so the app's copy matches.

## Required evidence before live

Each of these in the packaged app against the sandbox, recorded separately:

1. A new account checks out on a trial price with Paddle's test card, sees "Free until …", and can start agent work.
2. An account that has had a trial or a subscription, a new email on a Mac that's had a trial, and the same Gmail address with dots or a `+tag` all get the plain price at checkout. Clicking Monthly twice opens the same checkout; clicking Yearly after Monthly cancels the unpaid Monthly one.
3. A trial cancelled in the portal ends at its date; after that, saved work stays readable and new agent work is refused with the plan line.
4. Monthly and yearly checkout open in the browser with Paddle's test card; access appears within a minute.
5. Manage billing opens the portal; cancelling shows the end date; the period end removes access.
6. A failed renewal (Paddle's declining test card) shows "Payment failed" and ends access after the grace period.
7. An approved tester's access is unchanged before and after the migration.
8. A replayed webhook, a tampered body and an old timestamp are all refused (check `dervo_private.billing_events`).
9. A full refund from the Paddle dashboard ends access, and changing the card afterwards doesn't bring it back.
10. Agent work that's running when access ends: today the app's access check runs during turns, so a trial ending mid-turn may stop it. The "Make Dervo feel premium" thread owns that behavior.

## Going live

Only with Bryce's explicit go, after the evidence above: create the live Paddle account (Paddle reviews the business first), live prices, a live notification destination and API key; then switch `billing_settings.environment` to `live` with the live price IDs, replace the Vault secret, and set `DERVO_PADDLE_ENVIRONMENT=live`. The checkout function refuses to run if the key, the setting and the environment don't all agree.
