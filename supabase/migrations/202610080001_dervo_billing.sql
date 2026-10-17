-- Paid plans and the free trial (8 Oct 2026). Dervo becomes paid: a short free trial, then monthly or yearly.
--
-- Access still flows through public.dervo_memberships, so every existing check (dervo_access, release downloads,
-- feedback, usage and error reports) treats a trial or a paid plan the same as an approved beta account, and older apps
-- keep working without a change. What's new is who may write that row:
--   * an admin, as before (Testers page, invitations, dervo_set_membership), marked access_source 'admin';
--   * billing, marked 'billing', from a started trial or from a Paddle subscription event.
-- An admin's choice always wins: a revoked account stays revoked whatever billing says, and an approval with no end date
-- is never shortened. Existing rows default to 'admin', so nobody's current access changes.
--
-- Paddle (the merchant of record) sends subscription events to the dervo-billing-webhook Edge Function, which passes the
-- raw body and its Paddle-Signature header to dervo_billing_ingest() below with the publishable key. The signature is
-- checked here against a secret held in Supabase Vault, so no service-role key is involved anywhere.
--
-- Applying this to the hosted project needs Bryce's go, like every migration. Sandbox first: billing_settings starts in
-- 'sandbox' with no prices, so nothing can be bought until the sandbox price IDs are set. See supabase/BILLING.md.
begin;

alter table public.dervo_memberships add column access_source text not null default 'admin'
  check (access_source in ('admin', 'billing'));

-- One row of settings. Prices stay null until Bryce creates them in Paddle; nothing is purchasable until then.
create table dervo_private.billing_settings (
  id boolean primary key default true check (id),
  environment text not null default 'sandbox' check (environment in ('sandbox', 'live')),
  -- 'card': the free trial is Paddle's, on the two trial prices, so it needs a card and Paddle charges when it ends.
  -- 'app': the trial starts in the app with no card (dervo_start_trial). One setting either way.
  trial_mode text not null default 'card' check (trial_mode in ('card', 'app')),
  -- How long the trial lasts. In 'card' mode it must match the trial on the Paddle trial prices; it's only shown.
  trial_days integer not null default 7 check (trial_days between 1 and 30),
  -- How long a renewal that's late (webhook delay) or failing (past due) keeps access.
  grace_seconds integer not null default 172800 check (grace_seconds between 0 and 1209600),
  webhook_tolerance_seconds integer not null default 300 check (webhook_tolerance_seconds between 30 and 3600),
  month_price_id text check (month_price_id ~ '^pri_[a-z0-9]{26}$'),
  year_price_id text check (year_price_id ~ '^pri_[a-z0-9]{26}$'),
  -- The same plans with Paddle's free trial attached, for an account that has never had a trial or a subscription.
  month_trial_price_id text check (month_trial_price_id ~ '^pri_[a-z0-9]{26}$'),
  year_trial_price_id text check (year_trial_price_id ~ '^pri_[a-z0-9]{26}$'),
  check (month_price_id is distinct from year_price_id or month_price_id is null)
);
insert into dervo_private.billing_settings(id) values (true);

-- One trial per account, and one per Mac and per email (Gmail dots and +tags folded), whoever asks.
create table dervo_private.trials (
  user_id uuid primary key references auth.users(id) on delete cascade,
  started_at timestamptz not null default now(),
  ends_at timestamptz not null
);
-- Hashes only: the app sends a salted hash of the Mac's hardware UUID, and the email is hashed here. Deliberately no
-- foreign key: deleting an account mustn't free its Mac or email for another trial.
create table dervo_private.trial_identities (
  kind text not null check (kind in ('device', 'email')),
  key text not null check (key ~ '^[a-f0-9]{64}$'),
  user_id uuid not null,
  started_at timestamptz not null default now(),
  primary key (kind, key)
);

-- Every Dervo price that has ever been sold, so a subscriber on an older price keeps renewing after the prices for new
-- customers change. Filled from billing_settings by a trigger; only the settings decide what checkout sells.
create table dervo_private.billing_prices (
  price_id text primary key check (price_id ~ '^pri_[a-z0-9]{26}$'),
  environment text not null check (environment in ('sandbox', 'live')),
  billing_interval text not null check (billing_interval in ('month', 'year')),
  added_at timestamptz not null default now()
);

create table dervo_private.subscriptions (
  subscription_id text primary key check (subscription_id ~ '^sub_[a-z0-9]{26}$'),
  -- Sandbox rows never count once the settings say 'live', and live ones never count in sandbox.
  environment text not null check (environment in ('sandbox', 'live')),
  -- False for a Paddle trial bought by an account that had already had one (say, through Paddle.js directly): it then
  -- gives nothing until it's paid.
  trial_allowed boolean not null default true,
  user_id uuid not null references auth.users(id) on delete cascade,
  customer_id text not null check (customer_id ~ '^ctm_[a-z0-9]{26}$'),
  status text not null check (status in ('active', 'trialing', 'past_due', 'paused', 'canceled')),
  price_id text not null,
  billing_interval text not null check (billing_interval in ('month', 'year')),
  period_ends_at timestamptz,
  cancel_at timestamptz,
  past_due_since timestamptz,
  -- Set by a full refund or a chargeback. Paddle keeps such a subscription active, so later events (a card change, a
  -- scheduled change) must not bring its access back. Only a billing period that starts after the refund does.
  refunded_at timestamptz,
  event_at timestamptz not null,
  updated_at timestamptz not null default now()
);
create index subscriptions_user on dervo_private.subscriptions(user_id);

-- The caller's latest checkout, so a double click, or a second click before Paddle's webhook arrives, reopens the same
-- Paddle transaction instead of creating a second one that could charge again. The device hash is what the first
-- trialing webhook records against the trial, so a new email on the same Mac doesn't get another one.
create table dervo_private.checkouts (
  user_id uuid primary key references auth.users(id) on delete cascade,
  environment text not null check (environment in ('sandbox', 'live')),
  billing_interval text check (billing_interval in ('month', 'year')),
  transaction_id text check (transaction_id ~ '^txn_[a-z0-9]{26}$'),
  url text check (char_length(url) <= 2048 and url ~ '^https://'),
  opened_at timestamptz,
  -- A checkout being created right now, and for which interval. A claim older than 2 minutes is abandoned.
  claimed_at timestamptz,
  claim_interval text check (claim_interval in ('month', 'year')),
  device text check (device ~ '^[a-f0-9]{64}$')
);

-- Every delivered event once, by Paddle's event ID, so a retry or a replay changes nothing.
create table dervo_private.billing_events (
  event_id text primary key check (event_id ~ '^evt_[a-z0-9]{26}$'),
  event_type text not null check (char_length(event_type) <= 80),
  occurred_at timestamptz not null,
  received_at timestamptz not null default now(),
  outcome text not null
);

create function dervo_private.record_prices() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into dervo_private.billing_prices(price_id, environment, billing_interval)
    select p.id, new.environment, p.cycle
    from (values (new.month_price_id, 'month'), (new.month_trial_price_id, 'month'),
                 (new.year_price_id, 'year'), (new.year_trial_price_id, 'year')) as p(id, cycle)
    where p.id is not null
    on conflict (price_id) do nothing;
  return new;
end;
$$;
create trigger dervo_record_prices after insert or update on dervo_private.billing_settings
  for each row execute function dervo_private.record_prices();

alter table dervo_private.billing_prices enable row level security;
alter table dervo_private.billing_settings enable row level security;
alter table dervo_private.trials enable row level security;
alter table dervo_private.trial_identities enable row level security;
alter table dervo_private.subscriptions enable row level security;
alter table dervo_private.billing_events enable row level security;
alter table dervo_private.checkouts enable row level security;
revoke all on dervo_private.billing_prices, dervo_private.billing_settings, dervo_private.trials, dervo_private.trial_identities,
  dervo_private.subscriptions, dervo_private.billing_events, dervo_private.checkouts from public, anon, authenticated;

-- Until when a trial or a subscription gives access, or null. Paused and canceled subscriptions give none. A scheduled
-- cancellation ends at its date. A failing renewal keeps access for the grace period from when it started failing.
create function dervo_private.trial_until(member uuid) returns timestamptz
language sql stable security definer set search_path = '' as $$
  select max(t.ends_at) from dervo_private.trials t where t.user_id = member;
$$;
create function dervo_private.subscription_until(member uuid) returns timestamptz
language sql stable security definer set search_path = '' as $$
  select max(case
      when (s.status = 'active' or (s.status = 'trialing' and s.trial_allowed)) and s.period_ends_at is not null
        then least(s.period_ends_at + pg_catalog.make_interval(secs => b.grace_seconds), coalesce(s.cancel_at, 'infinity'))
      when s.status = 'past_due'
        then least(coalesce(s.past_due_since, s.event_at) + pg_catalog.make_interval(secs => b.grace_seconds),
                   coalesce(s.cancel_at, 'infinity'))
    end)
  from dervo_private.subscriptions s join dervo_private.billing_settings b on b.environment = s.environment
  where s.user_id = member;
$$;
create function dervo_private.billing_until(member uuid) returns timestamptz
language sql stable security definer set search_path = '' as $$
  select greatest(dervo_private.trial_until(member), dervo_private.subscription_until(member));
$$;

-- Whether an account has had its one trial: an app trial, or any subscription at all (a Paddle trial starts one).
create function dervo_private.trial_used(member uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from dervo_private.trials t where t.user_id = member)
      or exists(select 1 from dervo_private.subscriptions s join dervo_private.billing_settings b on b.environment = s.environment
                where s.user_id = member);
$$;

-- A subscription Paddle may still bill or recover. Checkout refuses while one exists, so nobody pays twice: they manage it.
create function dervo_private.open_subscription(member uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from dervo_private.subscriptions s join dervo_private.billing_settings b on b.environment = s.environment
                where s.user_id = member and s.status in ('active', 'trialing', 'past_due', 'paused'));
$$;

-- Any membership change that billing didn't make is an admin's choice.
create function dervo_private.membership_source() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if coalesce(current_setting('dervo.billing_sync', true), '') <> 'on'
     and (new.status is distinct from old.status or new.expires_at is distinct from old.expires_at) then
    new.access_source := 'admin';
  end if;
  return new;
end;
$$;
create trigger dervo_membership_source before update on public.dervo_memberships
  for each row execute function dervo_private.membership_source();

-- Bring one account's membership in line with its trial and subscriptions.
create function dervo_private.sync_access(member uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare
  m public.dervo_memberships%rowtype;
  ends timestamptz := dervo_private.billing_until(member);
begin
  select * into m from public.dervo_memberships where user_id = member for update;
  if not found or m.status = 'revoked' then
    return;
  end if;
  -- An admin's approval that lasts at least as long stays exactly as it is.
  if m.status = 'approved' and m.access_source = 'admin'
     and (m.expires_at is null or (m.expires_at > now() and (ends is null or ends <= m.expires_at))) then
    return;
  end if;
  perform pg_catalog.set_config('dervo.billing_sync', 'on', true);
  if ends is not null and ends > now() then
    update public.dervo_memberships set status = 'approved', expires_at = ends, access_source = 'billing', updated_at = now()
      where user_id = member;
  elsif m.status = 'approved' and m.access_source = 'billing' and (m.expires_at is null or m.expires_at > now()) then
    -- A refund or an immediate cancellation ends billing access now.
    update public.dervo_memberships set expires_at = now(), updated_at = now() where user_id = member;
  end if;
  perform pg_catalog.set_config('dervo.billing_sync', 'off', true);
end;
$$;

-- Founders are people an admin approved before launch, never someone a trial or a payment approved.
create or replace function dervo_private.record_founder() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.status = 'approved' and new.access_source = 'admin' and exists(
      select 1 from dervo_private.founder_window w where w.closed_at is null or w.closed_at > now()) then
    insert into dervo_private.founders(user_id) values (new.user_id) on conflict (user_id) do nothing;
  end if;
  return new;
end;
$$;

-- The email part of the one-trial rule: lowercased, +tag dropped, Gmail dots folded, then hashed.
create function dervo_private.email_key(address text) returns text
language plpgsql immutable set search_path = '' as $$
declare
  local text := split_part(lower(btrim(coalesce(address, ''))), '@', 1);
  domain text := split_part(lower(btrim(coalesce(address, ''))), '@', 2);
begin
  if local = '' or domain = '' then
    return null;
  end if;
  local := split_part(local, '+', 1);
  if domain in ('gmail.com', 'googlemail.com') then
    local := replace(local, '.', '');
    domain := 'gmail.com';
  end if;
  return encode(extensions.digest(convert_to(local || '@' || domain, 'UTF8'), 'sha256'), 'hex');
end;
$$;

-- Whether this account's email, or the given Mac, has already had a trial on another account. A Mac is only known when
-- the app sends its hash (checkout and the app trial); the email always is.
create function dervo_private.identity_used(member uuid, device text) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from dervo_private.trial_identities i
    where i.user_id <> member
      and ((i.kind = 'email' and i.key = dervo_private.email_key((select u.email from auth.users u where u.id = member)))
           or (i.kind = 'device' and i.key = device)));
$$;

-- Start the caller's free trial. One per account, Mac and email. It doesn't start for an account that already has
-- access (an admin's approval or a paid plan), or one an admin removed.
create function public.dervo_start_trial(device text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  m public.dervo_memberships%rowtype;
  days integer;
  email_hash text;
  ends timestamptz;
begin
  if caller is null or not exists(select 1 from auth.users u
      where u.id = caller and u.email_confirmed_at is not null and not coalesce(u.is_anonymous, false)) then
    raise exception 'Confirmed Dervo identity required' using errcode = '42501';
  end if;
  if device is null or device !~ '^[a-f0-9]{64}$' then
    raise exception 'Unsupported device' using errcode = '22023';
  end if;
  if (select b.trial_mode from dervo_private.billing_settings b) <> 'app' then
    return jsonb_build_object('state', 'not_offered', 'ends_at', null);
  end if;
  select * into m from public.dervo_memberships where user_id = caller for update;
  if not found then
    raise exception 'Dervo account not found' using errcode = '22023';
  end if;
  if m.status = 'revoked' then
    return jsonb_build_object('state', 'removed', 'ends_at', null);
  end if;
  if dervo_private.trial_used(caller) then
    return jsonb_build_object('state', 'already_used', 'ends_at', dervo_private.trial_until(caller));
  end if;
  if (m.status = 'approved' and (m.expires_at is null or m.expires_at > now()))
     or coalesce(dervo_private.subscription_until(caller) > now(), false) then
    return jsonb_build_object('state', 'not_needed', 'ends_at', null);
  end if;
  email_hash := dervo_private.email_key((select u.email from auth.users u where u.id = caller));
  -- Two starts for the same Mac or email take turns, so the checks below are exact. Different people don't wait.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('dervo-trial:' || least(device, coalesce(email_hash, device)), 0));
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('dervo-trial:' || greatest(device, coalesce(email_hash, device)), 0));
  if exists(select 1 from dervo_private.trial_identities i
      where (i.kind = 'device' and i.key = device) or (i.kind = 'email' and i.key = email_hash)) then
    return jsonb_build_object('state', 'used_elsewhere', 'ends_at', null);
  end if;
  select b.trial_days into days from dervo_private.billing_settings b;
  ends := now() + pg_catalog.make_interval(days => days);
  insert into dervo_private.trials(user_id, ends_at) values (caller, ends);
  insert into dervo_private.trial_identities(kind, key, user_id) values ('device', device, caller);
  if email_hash is not null then
    insert into dervo_private.trial_identities(kind, key, user_id) values ('email', email_hash, caller);
  end if;
  perform dervo_private.sync_access(caller);
  return jsonb_build_object('state', 'started', 'ends_at', ends);
end;
$$;
revoke all on function public.dervo_start_trial(text) from public, anon, authenticated;
grant execute on function public.dervo_start_trial(text) to authenticated;

-- The caller's plan, for Account in Settings. dervo_access() keeps its five fields because older apps refuse more.
create function public.dervo_plan() returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  m public.dervo_memberships%rowtype;
  b dervo_private.billing_settings%rowtype;
  s dervo_private.subscriptions%rowtype;
  trial_end timestamptz;
  paid_until timestamptz;
  admin_grant boolean;
  trial_prices boolean;
  plan text;
begin
  if caller is null then
    raise exception 'Dervo sign-in required' using errcode = '42501';
  end if;
  select * into b from dervo_private.billing_settings;
  select * into m from public.dervo_memberships where user_id = caller;
  trial_end := dervo_private.trial_until(caller);
  paid_until := dervo_private.subscription_until(caller);
  select * into s from dervo_private.subscriptions where user_id = caller and environment = b.environment
    order by (status in ('active', 'trialing', 'past_due', 'paused')) desc, event_at desc limit 1;
  -- A Paddle trial is a subscription that hasn't charged yet. It reads as a trial, ending when its first period does.
  if s.status = 'trialing' and s.trial_allowed and coalesce(paid_until > now(), false) then
    trial_end := greatest(trial_end, s.period_ends_at);
  end if;
  trial_prices := b.month_trial_price_id is not null and b.year_trial_price_id is not null;
  admin_grant := m.status = 'approved' and m.access_source = 'admin' and (m.expires_at is null or m.expires_at > now());
  plan := case
    when m.status = 'revoked' then 'removed'
    when coalesce(paid_until > now(), false) and s.status is distinct from 'trialing' then 'paid'
    when coalesce(paid_until > now(), false) then 'trial'
    when admin_grant then 'beta'
    when coalesce(trial_end > now(), false) then 'trial'
    else 'none' end;
  return jsonb_build_object(
    'plan', plan,
    'environment', b.environment,
    'trial', jsonb_build_object('days', b.trial_days, 'mode', b.trial_mode, 'ends_at', trial_end,
      'available', plan = 'none' and not dervo_private.trial_used(caller) and not dervo_private.identity_used(caller, null)
        and (b.trial_mode = 'app' or trial_prices)),
    'subscription', case when s.subscription_id is null then null else jsonb_build_object(
      'status', s.status, 'interval', s.billing_interval, 'period_ends_at', s.period_ends_at,
      'cancel_at', s.cancel_at, 'access_until', paid_until) end,
    'can_buy', plan not in ('removed', 'paid') and not dervo_private.open_subscription(caller) and b.month_price_id is not null and b.year_price_id is not null,
    'can_manage', s.subscription_id is not null and s.status <> 'canceled');
end;
$$;
revoke all on function public.dervo_plan() from public, anon, authenticated;
grant execute on function public.dervo_plan() to authenticated;

-- What the dervo-billing Edge Function needs to open checkout or the customer portal for the caller: never anyone else.
create function public.dervo_billing_context(device text default null) returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  plan jsonb := public.dervo_plan();
  b dervo_private.billing_settings%rowtype;
begin
  select * into b from dervo_private.billing_settings;
  return jsonb_build_object(
    'user_id', caller,
    'plan', plan->>'plan',
    'environment', b.environment,
    'month_price_id', b.month_price_id,
    'year_price_id', b.year_price_id,
    -- Checkout uses the trial prices only for an account that's never had a trial or a subscription, on a Mac and with
    -- an email that haven't had a trial on another account either.
    'trial_eligible', b.trial_mode = 'card' and plan->>'plan' = 'none' and not dervo_private.trial_used(caller)
      and coalesce(device ~ '^[a-f0-9]{64}$', false) and not dervo_private.identity_used(caller, device),
    'open_subscription', dervo_private.open_subscription(caller),
    'month_trial_price_id', b.month_trial_price_id,
    'year_trial_price_id', b.year_trial_price_id,
    'customer_id', (select s.customer_id from dervo_private.subscriptions s
                    where s.user_id = caller and s.environment = b.environment order by s.event_at desc limit 1),
    'subscription_ids', coalesce((select jsonb_agg(s.subscription_id order by s.event_at desc)
                                  from dervo_private.subscriptions s
                                  where s.user_id = caller and s.environment = b.environment and s.status <> 'canceled'), '[]'::jsonb));
end;
$$;
revoke all on function public.dervo_billing_context(text) from public, anon, authenticated;
grant execute on function public.dervo_billing_context(text) to authenticated;

-- Checkout, one at a time per account. begin either hands back the caller's open checkout for the same interval (so a
-- double click reopens it), says one is being created right now, or claims the right to create one. The claim names
-- the caller's previous open checkout, which the Edge Function cancels in Paddle (after checking it's this caller's)
-- before creating the new one, so two open checkouts never exist for one account.
create function public.dervo_billing_checkout_begin(billing_interval text, device text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  env text := (select b.environment from dervo_private.billing_settings b);
  c dervo_private.checkouts%rowtype;
  previous text;
begin
  if caller is null then
    raise exception 'Dervo sign-in required' using errcode = '42501';
  end if;
  if billing_interval is null or billing_interval not in ('month', 'year') or device is null or device !~ '^[a-f0-9]{64}$' then
    raise exception 'Unsupported checkout' using errcode = '22023';
  end if;
  insert into dervo_private.checkouts(user_id, environment) values (caller, env) on conflict (user_id) do nothing;
  select * into c from dervo_private.checkouts where user_id = caller for update;
  if c.claimed_at > now() - interval '2 minutes' then
    return jsonb_build_object('state', 'busy');
  end if;
  -- A Paddle checkout stays payable for a day; after that a fresh one is created.
  if c.environment = env and c.transaction_id is not null and c.url is not null and c.opened_at > now() - interval '1 day' then
    if c.billing_interval = dervo_billing_checkout_begin.billing_interval then
      update dervo_private.checkouts set device = dervo_billing_checkout_begin.device where user_id = caller;
      return jsonb_build_object('state', 'reuse', 'transaction_id', c.transaction_id, 'url', c.url);
    end if;
    previous := c.transaction_id;
  end if;
  update dervo_private.checkouts set claimed_at = now(), claim_interval = dervo_billing_checkout_begin.billing_interval,
      device = dervo_billing_checkout_begin.device
    where user_id = caller;
  return jsonb_build_object('state', 'claimed', 'cancel', previous);
end;
$$;
create function public.dervo_billing_checkout_finish(transaction_id text, url text) returns void
language plpgsql security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
begin
  if caller is null then
    raise exception 'Dervo sign-in required' using errcode = '42501';
  end if;
  if transaction_id is null or transaction_id !~ '^txn_[a-z0-9]{26}$' or url is null or char_length(url) > 2048
     or url !~ '^https://' then
    raise exception 'Unsupported checkout' using errcode = '22023';
  end if;
  update dervo_private.checkouts c set transaction_id = dervo_billing_checkout_finish.transaction_id,
      url = dervo_billing_checkout_finish.url, billing_interval = c.claim_interval, opened_at = now(),
      environment = (select b.environment from dervo_private.billing_settings b), claimed_at = null, claim_interval = null
    where c.user_id = caller and c.claimed_at is not null;
  if not found then
    raise exception 'No checkout is being created' using errcode = '55000';
  end if;
end;
$$;
create function public.dervo_billing_checkout_release() returns void
language sql security definer set search_path = '' as $$
  update dervo_private.checkouts set claimed_at = null, claim_interval = null where user_id = (select auth.uid());
$$;
revoke all on function public.dervo_billing_checkout_begin(text, text), public.dervo_billing_checkout_finish(text, text),
  public.dervo_billing_checkout_release() from public, anon, authenticated;
grant execute on function public.dervo_billing_checkout_begin(text, text), public.dervo_billing_checkout_finish(text, text),
  public.dervo_billing_checkout_release() to authenticated;

-- One Paddle webhook delivery: the raw body and the Paddle-Signature header, exactly as received. Anyone may call it
-- through the publishable key, so nothing happens until the signature matches the Vault secret. A rejected or failed
-- delivery raises and changes nothing, so Paddle retries it.
create function public.dervo_billing_ingest(payload text, signature text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  b dervo_private.billing_settings%rowtype;
  secret text;
  part text;
  stamp text;
  digests text[] := '{}';
  event jsonb;
  d jsonb;
  occurred timestamptz;
  sub_id text;
  sub_status text;
  customer text;
  price text;
  cycle text;
  member uuid;
  existing dervo_private.subscriptions%rowtype;
  had_row boolean;
  allowed boolean;
  starts timestamptz;
  email_hash text;
  device text;
  result text;
begin
  if payload is null or octet_length(payload) > 262144 or signature is null or octet_length(signature) > 1024 then
    raise exception 'Unsupported billing delivery' using errcode = '22023';
  end if;
  select * into b from dervo_private.billing_settings;
  select s.decrypted_secret into secret from vault.decrypted_secrets s where s.name = 'dervo_paddle_webhook_secret' limit 1;
  if coalesce(secret, '') = '' then
    raise exception 'Billing webhook isn''t configured' using errcode = '55000';
  end if;
  -- Paddle-Signature is "ts=<unix seconds>;h1=<hex>", with more than one h1 while a secret is being rotated.
  foreach part in array string_to_array(signature, ';') loop
    if part ~ '^ts=[0-9]{1,12}$' then
      stamp := substr(part, 4);
    elsif part ~ '^h1=[a-f0-9]{64}$' then
      digests := digests || substr(part, 4);
    end if;
  end loop;
  if stamp is null or cardinality(digests) = 0
     or abs(extract(epoch from now()) - stamp::bigint) > b.webhook_tolerance_seconds
     or not (encode(extensions.hmac(convert_to(stamp || ':' || payload, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'), 'hex')
             = any(digests)) then
    raise exception 'Billing signature rejected' using errcode = '42501';
  end if;

  begin
    event := payload::jsonb;
    occurred := (event->>'occurred_at')::timestamptz;
  exception when others then
    raise exception 'Unsupported billing event' using errcode = '22023';
  end;
  if jsonb_typeof(event) is distinct from 'object' or coalesce(event->>'event_id', '') !~ '^evt_[a-z0-9]{26}$'
     or jsonb_typeof(event->'event_type') is distinct from 'string' or occurred is null
     or jsonb_typeof(event->'data') is distinct from 'object' then
    raise exception 'Unsupported billing event' using errcode = '22023';
  end if;
  insert into dervo_private.billing_events(event_id, event_type, occurred_at, outcome)
    values (event->>'event_id', left(event->>'event_type', 80), occurred, 'received')
    on conflict (event_id) do nothing;
  if not found then
    return jsonb_build_object('state', 'duplicate');
  end if;

  result := 'applied';
  d := event->'data';
  sub_id := d->>'id';
  sub_status := d->>'status';
  customer := d->>'customer_id';
  if event->>'event_type' in ('adjustment.created', 'adjustment.updated') then
    -- Paddle doesn't cancel a subscription when it refunds it. A full refund or a chargeback that's gone through ends
    -- that subscription's access here, and a partial refund or credit changes nothing.
    if coalesce(d->>'subscription_id', '') ~ '^sub_[a-z0-9]{26}$' and d->>'status' = 'approved'
       and (d->>'action' = 'chargeback' or (d->>'action' = 'refund' and d->>'type' = 'full')) then
      update dervo_private.subscriptions set status = 'canceled', cancel_at = least(coalesce(cancel_at, occurred), occurred),
          refunded_at = greatest(coalesce(refunded_at, occurred), occurred), event_at = greatest(event_at, occurred),
          updated_at = now()
        where subscription_id = d->>'subscription_id' and environment = b.environment returning user_id into member;
      if member is null and occurred > now() - interval '3 days' then
        -- The subscription's own events may still be on their way. Raising makes Paddle deliver this again later.
        raise exception 'Refund for an unknown subscription' using errcode = '55000';
      elsif member is null then
        -- Still unknown after 3 days of retries: recorded, so Paddle stops retrying, and visible in billing_events.
        result := 'unknown_subscription';
      else
        perform dervo_private.sync_access(member);
        result := 'refunded';
      end if;
    else
      result := 'ignored';
    end if;
  elsif event->>'event_type' not like 'subscription.%' then
    result := 'ignored';
  elsif coalesce(sub_id, '') !~ '^sub_[a-z0-9]{26}$' or coalesce(customer, '') !~ '^ctm_[a-z0-9]{26}$'
        or sub_status is null or sub_status not in ('active', 'trialing', 'past_due', 'paused', 'canceled')
        or jsonb_typeof(d->'items') is distinct from 'array' or jsonb_array_length(d->'items') <> 1 then
    result := 'unsupported';
  else
    -- Only Dervo's own two prices grant anything, so a checkout for another product (or a crafted one) can't.
    price := d->'items'->0->'price'->>'id';
    select p.billing_interval into cycle from dervo_private.billing_prices p
      where p.price_id = price and p.environment = b.environment;
    select * into existing from dervo_private.subscriptions where subscription_id = sub_id for update;
    had_row := found;
    if cycle is null then
      -- Not one of Dervo's prices. Recorded and not retried: it can't grant anything.
      result := 'unknown_price';
    elsif had_row and occurred < existing.event_at then
      result := 'stale';
    else
      if had_row then
        -- A subscription belongs to the account that bought it, whatever later custom data says.
        member := existing.user_id;
      elsif coalesce(d->'custom_data'->>'dervo_user_id', '') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
        member := (d->'custom_data'->>'dervo_user_id')::uuid;
        if not exists(select 1 from auth.users u where u.id = member) then
          member := null;
        end if;
      end if;
      if member is null then
        -- A Dervo price with no account to give it to: raise so Paddle retries, and it shows in Paddle's failed
        -- deliveries for Bryce to look at and replay, instead of a payment that silently grants nothing.
        raise exception 'Billing event for an unknown Dervo account' using errcode = '55000';
      else
        begin
          starts := (d->'current_billing_period'->>'starts_at')::timestamptz;
        exception when invalid_datetime_format or datetime_field_overflow or invalid_text_representation then
          raise exception 'Unsupported billing event' using errcode = '22023';
        end;
        allowed := had_row and existing.trial_allowed;
        if not had_row then
          -- A new subscription gets a free trial only if this account, its email and the Mac it checked out from have
          -- never had one. Checks on the same email or Mac take turns, so two accounts can't both get one at once.
          email_hash := dervo_private.email_key((select u.email from auth.users u where u.id = member));
          select c.device into device from dervo_private.checkouts c where c.user_id = member;
          perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended('dervo-trial:' || k, 0))
            from unnest(array[email_hash, device]) k where k is not null order by k;
          allowed := not dervo_private.trial_used(member) and not dervo_private.identity_used(member, device);
          if sub_status = 'trialing' and allowed then
            insert into dervo_private.trial_identities(kind, key, user_id)
              select 'email', email_hash, member where email_hash is not null
              union all select 'device', device, member where device is not null
              on conflict (kind, key) do nothing;
          end if;
        end if;
        begin
          insert into dervo_private.subscriptions as s (subscription_id, environment, trial_allowed, user_id, customer_id,
              status, price_id, billing_interval, period_ends_at, cancel_at, past_due_since, event_at, updated_at)
            values (sub_id, b.environment, allowed, member, customer, sub_status, price, cycle,
              (d->'current_billing_period'->>'ends_at')::timestamptz,
              case when d->'scheduled_change'->>'action' = 'cancel' then (d->'scheduled_change'->>'effective_at')::timestamptz end,
              case when sub_status = 'past_due' then occurred end, occurred, now())
            on conflict (subscription_id) do update set
              -- A refunded subscription stays canceled until a billing period starts after the refund (a real renewal).
              status = case when s.refunded_at is not null and not coalesce(starts > s.refunded_at, false)
                            then 'canceled' else excluded.status end,
              cancel_at = case when s.refunded_at is not null and not coalesce(starts > s.refunded_at, false)
                               then s.cancel_at else excluded.cancel_at end,
              refunded_at = case when coalesce(starts > s.refunded_at, false) then null else s.refunded_at end,
              price_id = excluded.price_id, billing_interval = excluded.billing_interval,
              period_ends_at = excluded.period_ends_at,
              past_due_since = case when excluded.status = 'past_due' then coalesce(s.past_due_since, excluded.past_due_since) end,
              event_at = excluded.event_at, updated_at = now();
        exception when invalid_datetime_format or datetime_field_overflow or invalid_text_representation then
          raise exception 'Unsupported billing event' using errcode = '22023';
        end;
        perform dervo_private.sync_access(member);
      end if;
    end if;
  end if;
  update dervo_private.billing_events set outcome = result where event_id = event->>'event_id';
  return jsonb_build_object('state', result);
end;
$$;
revoke all on function public.dervo_billing_ingest(text, text) from public, anon, authenticated;
grant execute on function public.dervo_billing_ingest(text, text) to anon;

revoke all on function dervo_private.trial_until(uuid), dervo_private.subscription_until(uuid),
  dervo_private.billing_until(uuid), dervo_private.trial_used(uuid), dervo_private.open_subscription(uuid),
  dervo_private.record_prices(), dervo_private.membership_source(), dervo_private.sync_access(uuid),
  dervo_private.email_key(text), dervo_private.identity_used(uuid, text) from public, anon, authenticated;

commit;

-- Going live, with Bryce's go, in the project's SQL editor, after creating live prices in Paddle:
--   update dervo_private.billing_settings set environment = 'live', month_price_id = 'pri_…', year_price_id = 'pri_…',
--     month_trial_price_id = 'pri_…', year_trial_price_id = 'pri_…';
-- and replacing the Vault secret dervo_paddle_webhook_secret with the live endpoint's secret. Sandbox subscriptions
-- stop counting at once (they're filtered by environment). Sandbox app trials do still count as used.
