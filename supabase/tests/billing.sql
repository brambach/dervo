-- Paid plans and the free trial (migrations/202610080001_dervo_billing.sql), on an isolated PostgreSQL cluster with
-- Supabase-shaped test schemas. Checks the trial rules, webhook signatures and ordering, and that an admin's choice
-- always wins over billing. Not hosted Vault, PostgREST or Paddle delivery.
create function pg_temp.assert_true(value boolean, label text) returns void language plpgsql as $$
begin if value is distinct from true then raise exception 'Assertion failed: %', label; end if; end;
$$;
create function pg_temp.fails(statement text, code text) returns void language plpgsql as $$
declare got text;
begin
  begin execute statement;
  exception when others then got := sqlstate;
  end;
  if got is distinct from code then raise exception 'Expected % from %, got %', code, statement, got; end if;
end;
$$;
create function pg_temp.as_user(member text) returns void language sql as $$
  select set_config('request.jwt.claim.sub', member, false);
$$;
create function pg_temp.sig(body text, stamp bigint, secret text default 'pdl_ntfset_test_secret') returns text language sql as $$
  select 'ts=' || stamp || ';h1=' ||
    encode(extensions.hmac(convert_to(stamp || ':' || body, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'), 'hex');
$$;
create function pg_temp.now_s() returns bigint language sql as $$ select extract(epoch from now())::bigint $$;
create function pg_temp.event(id text, kind text, occurred timestamptz, sub text, status text, price text,
    member text, ends timestamptz, cancel timestamptz default null) returns text language sql as $$
  select jsonb_build_object('event_id', id, 'event_type', kind, 'occurred_at', occurred, 'notification_id', 'ntf_x',
    'data', jsonb_build_object('id', sub, 'status', status, 'customer_id', 'ctm_aaaaaaaaaaaaaaaaaaaaaaaaaa',
      'custom_data', case when member is null then null else jsonb_build_object('dervo_user_id', member) end,
      'current_billing_period', jsonb_build_object('starts_at', occurred, 'ends_at', ends),
      'scheduled_change', case when cancel is null then null else jsonb_build_object('action', 'cancel', 'effective_at', cancel) end,
      'items', jsonb_build_array(jsonb_build_object('quantity', 1, 'price', jsonb_build_object('id', price,
        'billing_cycle', jsonb_build_object('interval', 'month', 'frequency', 1))))))::text
$$;
create function pg_temp.member(id text) returns public.dervo_memberships language sql as $$
  select * from public.dervo_memberships where user_id = id::uuid
$$;
create function pg_temp.access(id text) returns jsonb language plpgsql as $$
declare result jsonb;
begin
  perform pg_temp.as_user(id);
  set local role authenticated;
  result := public.dervo_access();
  reset role;
  return result;
end;
$$;

-- What the functions are: definer rights, an empty search_path, and the right callers.
select pg_temp.assert_true((select count(*) = 9 and bool_and(prosecdef and proconfig @> array['search_path=""'])
  from pg_proc where proname in ('dervo_start_trial', 'dervo_plan', 'dervo_billing_context', 'dervo_billing_ingest',
    'sync_access', 'trial_until', 'subscription_until', 'billing_until', 'membership_source')), 'definer rights and empty search_path');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_start_trial(text)', 'execute'), 'anon starts no trial');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_plan()', 'execute'), 'anon reads no plan');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_billing_context(text)', 'execute'), 'anon reads no billing context');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_billing_checkout_begin(text, text)', 'execute')
  and not has_function_privilege('anon', 'public.dervo_billing_checkout_finish(text, text)', 'execute'), 'anon opens no checkout');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'dervo_private.checkouts', 'select'), 'checkouts are private');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.dervo_start_trial(text)', 'execute'), 'signed-in users start trials');
select pg_temp.assert_true(has_function_privilege('anon', 'public.dervo_billing_ingest(text, text)', 'execute'), 'the webhook forwarder may deliver');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'dervo_private.sync_access(uuid)', 'execute'), 'nobody syncs directly');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'dervo_private.subscriptions', 'select'), 'subscriptions are private');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'dervo_private.trial_identities', 'select'), 'trial identities are private');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_memberships', 'update'), 'members still can''t write memberships');
select pg_temp.assert_true((select pg_get_functiondef('public.dervo_access()'::regprocedure) not like '%billing%'), 'dervo_access unchanged');

insert into auth.users(id, email) values
 ('b0000000-0000-0000-0000-000000000001', 'Trial.Person+dervo@gmail.com'),
 ('b0000000-0000-0000-0000-000000000002', 'second@example.test'),
 ('b0000000-0000-0000-0000-000000000003', 'trialperson@googlemail.com'),
 ('b0000000-0000-0000-0000-000000000004', 'tester@example.test'),
 ('b0000000-0000-0000-0000-000000000005', 'removed@example.test'),
 ('b0000000-0000-0000-0000-000000000006', 'payer@example.test'),
 ('b0000000-0000-0000-0000-000000000007', 'unconfirmed@example.test');
update auth.users set email_confirmed_at = null where id = 'b0000000-0000-0000-0000-000000000007';
-- An approved beta tester with no end date, and one an admin removed. Existing rows are the admin's.
update public.dervo_memberships set status = 'approved' where user_id = 'b0000000-0000-0000-0000-000000000004';
update public.dervo_memberships set status = 'revoked' where user_id = 'b0000000-0000-0000-0000-000000000005';
select pg_temp.assert_true((select bool_and(access_source = 'admin') from public.dervo_memberships), 'every existing row is the admin''s');
-- The default trial takes a card through Paddle; these first checks are the no-card app trial, one setting away.
select pg_temp.assert_true((select trial_mode = 'card' from dervo_private.billing_settings), 'the default trial takes a card');
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000001');
select pg_temp.assert_true(public.dervo_start_trial(repeat('a', 64))->>'state' = 'not_offered', 'no app trial in card mode');
select pg_temp.assert_true(not (public.dervo_plan()->'trial'->>'available')::boolean, 'and no card trial until trial prices exist');
reset role;
update dervo_private.billing_settings set trial_mode = 'app';
-- Reopen the founder window, so the test can show a trial never makes a founder.
update dervo_private.founder_window set closed_at = null;

-- The trial. Starting one approves the account until it ends, as billing, and doesn't make a founder.
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000001');
select pg_temp.assert_true((public.dervo_plan()->>'plan') = 'none' and (public.dervo_plan()->'trial'->>'available')::boolean
  and (public.dervo_plan()->'trial'->>'days')::int = 7 and not (public.dervo_plan()->>'can_buy')::boolean, 'a new account can start a trial, and nothing is for sale yet');
select pg_temp.fails($$select public.dervo_start_trial('not-a-hash')$$, '22023');
select pg_temp.assert_true(public.dervo_start_trial(repeat('a', 64))->>'state' = 'started', 'trial starts');
select pg_temp.assert_true(public.dervo_start_trial(repeat('a', 64))->>'state' = 'already_used', 'one trial per account');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'trial' and not (public.dervo_plan()->'trial'->>'available')::boolean, 'plan reads trial');
select pg_temp.fails('select * from dervo_private.trials', '42501');
reset role;
select pg_temp.assert_true((select status = 'approved' and access_source = 'billing'
  and expires_at between now() + interval '7 days' - interval '1 minute' and now() + interval '7 days'
  from pg_temp.member('b0000000-0000-0000-0000-000000000001')), 'the trial approves until it ends');
select pg_temp.assert_true((select (a->>'allowed')::boolean and a->>'status' = 'approved' and a ? 'expires_at'
  from pg_temp.access('b0000000-0000-0000-0000-000000000001') a), 'older apps see an approved, dated membership');
select pg_temp.assert_true(not exists(select 1 from dervo_private.founders where user_id = 'b0000000-0000-0000-0000-000000000001'), 'a trial isn''t a founder');

-- One per Mac and per email: the same device, or the same Gmail address with dots and a +tag, gets nothing.
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000002');
select pg_temp.assert_true(public.dervo_start_trial(repeat('a', 64))->>'state' = 'used_elsewhere', 'one trial per Mac');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000003');
select pg_temp.assert_true(public.dervo_start_trial(repeat('c', 64))->>'state' = 'used_elsewhere', 'one trial per email, Gmail folded');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000004');
select pg_temp.assert_true(public.dervo_start_trial(repeat('d', 64))->>'state' = 'not_needed', 'an approved tester needs no trial');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'beta', 'an approved tester reads beta');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000005');
select pg_temp.assert_true(public.dervo_start_trial(repeat('e', 64))->>'state' = 'removed', 'a removed account gets no trial');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'removed', 'and reads removed');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000007');
select pg_temp.fails($$select public.dervo_start_trial(repeat('f', 64))$$, '42501');
select pg_temp.as_user('');
select pg_temp.fails($$select public.dervo_start_trial(repeat('f', 64))$$, '42501');
select pg_temp.fails($$select public.dervo_plan()$$, '42501');
reset role;
select pg_temp.assert_true((select status = 'pending' from pg_temp.member('b0000000-0000-0000-0000-000000000002')), 'a refused trial changes nothing');
select pg_temp.assert_true((select status = 'approved' and expires_at is null and access_source = 'admin'
  from pg_temp.member('b0000000-0000-0000-0000-000000000004')), 'the tester is untouched');

-- The webhook. Nothing works until the secret and prices are set.
set role anon;
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', '{}', 'ts=1;h1=' || repeat('0', 64)), '55000');
reset role;
insert into vault.decrypted_secrets values ('dervo_paddle_webhook_secret', 'pdl_ntfset_test_secret');
update dervo_private.billing_settings set month_price_id = 'pri_month000000000000000000000', year_price_id = 'pri_year0000000000000000000000';

create temporary table deliveries(name text primary key, body text);
grant select on deliveries to anon;
insert into deliveries values
 ('created', pg_temp.event('evt_000000000000000000000000a1', 'subscription.created', now() - interval '1 minute',
   'sub_000000000000000000000000p1', 'active', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-000000000006', now() + interval '30 days')),
 ('stale', pg_temp.event('evt_000000000000000000000000a0', 'subscription.updated', now() - interval '10 minutes',
   'sub_000000000000000000000000p1', 'canceled', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-000000000006', null)),
 ('other_price', pg_temp.event('evt_000000000000000000000000a2', 'subscription.created', now(),
   'sub_000000000000000000000000p2', 'active', 'pri_other000000000000000000000', 'b0000000-0000-0000-0000-000000000002', now() + interval '30 days')),
 ('hijack', pg_temp.event('evt_000000000000000000000000a3', 'subscription.updated', now(),
   'sub_000000000000000000000000p1', 'active', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-000000000002', now() + interval '30 days')),
 ('cancel_later', pg_temp.event('evt_000000000000000000000000a4', 'subscription.updated', now() + interval '1 second',
   'sub_000000000000000000000000p1', 'active', 'pri_month000000000000000000000', null, now() + interval '30 days', now() + interval '30 days')),
 ('canceled', pg_temp.event('evt_000000000000000000000000a5', 'subscription.canceled', now() + interval '2 seconds',
   'sub_000000000000000000000000p1', 'canceled', 'pri_month000000000000000000000', null, null)),
 ('tester_pays', pg_temp.event('evt_000000000000000000000000a6', 'subscription.created', now(),
   'sub_000000000000000000000000p4', 'active', 'pri_year0000000000000000000000', 'b0000000-0000-0000-0000-000000000004', now() + interval '365 days')),
 ('removed_pays', pg_temp.event('evt_000000000000000000000000a7', 'subscription.created', now(),
   'sub_000000000000000000000000p5', 'active', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-000000000005', now() + interval '30 days')),
 ('stranger', pg_temp.event('evt_000000000000000000000000a8', 'subscription.created', now(),
   'sub_000000000000000000000000p6', 'active', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-0000000000ff', now() + interval '30 days')),
 ('past_due', pg_temp.event('evt_000000000000000000000000a9', 'subscription.past_due', now() + interval '3 seconds',
   'sub_000000000000000000000000p2', 'past_due', 'pri_month000000000000000000000', 'b0000000-0000-0000-0000-000000000002', now() + interval '60 days')),
 ('transaction', '{"event_id":"evt_000000000000000000000000b1","event_type":"transaction.completed","occurred_at":"2026-10-08T00:00:00Z","data":{"id":"txn_1"}}');

set role anon;
-- A wrong secret, an old or future timestamp, a missing digest, or an edited body is refused before anything is read.
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, pg_temp.sig(body, pg_temp.now_s(), 'wrong')), '42501') from deliveries where name = 'created';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, pg_temp.sig(body, pg_temp.now_s() - 3600)), '42501') from deliveries where name = 'created';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, pg_temp.sig(body, pg_temp.now_s() + 3600)), '42501') from deliveries where name = 'created';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, 'ts=' || pg_temp.now_s()), '42501') from deliveries where name = 'created';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body || ' ', pg_temp.sig(body, pg_temp.now_s())), '42501') from deliveries where name = 'created';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', 'not json', pg_temp.sig('not json', pg_temp.now_s())), '22023');
-- A rotated secret sends two digests; either matching is enough.
select pg_temp.assert_true(public.dervo_billing_ingest(body,
    pg_temp.sig(body, pg_temp.now_s(), 'old') || ';h1=' || split_part(pg_temp.sig(body, pg_temp.now_s()), 'h1=', 2))->>'state' = 'applied', 'a paid subscription applies')
  from deliveries where name = 'created';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'duplicate', 'a retry changes nothing')
  from deliveries where name = 'created';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'stale', 'an older event doesn''t undo a newer one')
  from deliveries where name = 'stale';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'unknown_price', 'another price grants nothing')
  from deliveries where name = 'other_price';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'applied', 'a later update applies')
  from deliveries where name = 'hijack';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, pg_temp.sig(body, pg_temp.now_s())), '55000')
  from deliveries where name = 'stranger';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'ignored', 'other event types are only recorded')
  from deliveries where name = 'transaction';
reset role;
select pg_temp.assert_true((select status = 'approved' and access_source = 'billing'
  and expires_at between now() + interval '32 days' - interval '1 minute' and now() + interval '32 days'
  from pg_temp.member('b0000000-0000-0000-0000-000000000006')), 'paid until the period ends, plus two days of grace');
select pg_temp.assert_true((select user_id = 'b0000000-0000-0000-0000-000000000006'::uuid from dervo_private.subscriptions
  where subscription_id = 'sub_000000000000000000000000p1'), 'later custom data can''t move a subscription to another account');
select pg_temp.assert_true((select status = 'pending' from pg_temp.member('b0000000-0000-0000-0000-000000000002')), 'the other account still has nothing');
select pg_temp.assert_true(not exists(select 1 from dervo_private.founders where user_id = 'b0000000-0000-0000-0000-000000000006'), 'a payer isn''t a founder');

set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000006');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'paid' and public.dervo_plan()->'subscription'->>'interval' = 'month'
  and (public.dervo_plan()->>'can_manage')::boolean and not (public.dervo_plan()->>'can_buy')::boolean
  and not (public.dervo_plan()->'trial'->>'available')::boolean, 'plan reads paid');
select pg_temp.assert_true(public.dervo_billing_context()->>'customer_id' = 'ctm_aaaaaaaaaaaaaaaaaaaaaaaaaa'
  and public.dervo_billing_context()->'subscription_ids' = '["sub_000000000000000000000000p1"]'::jsonb, 'the caller''s own billing context');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000002');
select pg_temp.assert_true(public.dervo_billing_context()->>'customer_id' is null
  and (public.dervo_plan()->>'can_buy')::boolean, 'nobody else''s customer, and now prices are set');
reset role;

-- A scheduled cancellation ends at its date; the actual cancellation (or a refund) ends access now.
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name = 'cancel_later';
reset role;
select pg_temp.assert_true((select expires_at between now() + interval '30 days' - interval '1 minute' and now() + interval '30 days'
  from pg_temp.member('b0000000-0000-0000-0000-000000000006')), 'a scheduled cancel keeps access to the period end, without grace');
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name = 'canceled';
reset role;
select pg_temp.assert_true((select not (a->>'allowed')::boolean from pg_temp.access('b0000000-0000-0000-0000-000000000006') a), 'a canceled plan ends access');
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000006');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'none' and not (public.dervo_plan()->'trial'->>'available')::boolean,
  'someone who has paid before doesn''t get a trial afterwards');
select pg_temp.assert_true(public.dervo_billing_context()->'subscription_ids' = '[]'::jsonb, 'a canceled subscription isn''t managed');
reset role;

-- Admin choices win: a tester with no end date keeps exactly that, and a removed account stays removed.
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name in ('tester_pays', 'removed_pays');
reset role;
select pg_temp.assert_true((select status = 'approved' and expires_at is null and access_source = 'admin'
  from pg_temp.member('b0000000-0000-0000-0000-000000000004')), 'paying never shortens an admin''s open-ended approval');
select pg_temp.assert_true((select status = 'revoked' from pg_temp.member('b0000000-0000-0000-0000-000000000005')), 'paying never undoes a removal');

-- A failing renewal keeps access for the grace period from when it started failing, not the next period.
update dervo_private.subscriptions set status = 'canceled' where subscription_id = 'sub_000000000000000000000000p2';
insert into dervo_private.subscriptions(subscription_id, environment, user_id, customer_id, status, price_id, billing_interval, period_ends_at, event_at)
  values ('sub_000000000000000000000000p2', 'sandbox', 'b0000000-0000-0000-0000-000000000002', 'ctm_bbbbbbbbbbbbbbbbbbbbbbbbbb', 'active',
    'pri_month000000000000000000000', 'month', now() + interval '1 day', now() - interval '1 day')
  on conflict (subscription_id) do update set status = 'active', user_id = excluded.user_id, event_at = excluded.event_at;
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name = 'past_due';
reset role;
select pg_temp.assert_true((select status = 'approved' and expires_at between now() + interval '2 days' - interval '1 minute' and now() + interval '2 days' + interval '5 seconds'
  from pg_temp.member('b0000000-0000-0000-0000-000000000002')), 'past due keeps two days of grace, not sixty');

-- An admin changing a billing membership makes it the admin's, so billing won't touch it again.
insert into auth.users(id, email) values ('b0000000-0000-0000-0000-0000000000ad', 'admin@example.test');
insert into dervo_private.admins(user_id) values ('b0000000-0000-0000-0000-0000000000ad');
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000ad');
set role authenticated;
select public.dervo_set_membership('b0000000-0000-0000-0000-000000000001', 'revoked');
reset role;
select pg_temp.assert_true((select status = 'revoked' and access_source = 'admin' from pg_temp.member('b0000000-0000-0000-0000-000000000001')), 'an admin removal is the admin''s');

-- Refunds: Paddle doesn't cancel on a refund, so a full refund or chargeback ends access; a partial one doesn't.
create function pg_temp.adjustment(id text, sub text, action text, kind text, status text) returns text language sql as $$
  select jsonb_build_object('event_id', id, 'event_type', 'adjustment.updated', 'occurred_at', now() + interval '10 seconds',
    'data', jsonb_build_object('id', 'adj_1', 'action', action, 'type', kind, 'status', status, 'subscription_id', sub))::text
$$;
insert into deliveries values
 ('refund_partial', pg_temp.adjustment('evt_000000000000000000000000c1', 'sub_000000000000000000000000p4', 'refund', 'partial', 'approved')),
 ('refund_pending', pg_temp.adjustment('evt_000000000000000000000000c2', 'sub_000000000000000000000000p4', 'refund', 'full', 'pending_approval')),
 ('refund_full', pg_temp.adjustment('evt_000000000000000000000000c3', 'sub_000000000000000000000000p7', 'refund', 'full', 'approved')),
 ('refund_unknown', pg_temp.adjustment('evt_000000000000000000000000c4', 'sub_00000000000000000000000zzz', 'chargeback', 'full', 'approved')),
 ('paid7', pg_temp.event('evt_000000000000000000000000c0', 'subscription.created', now(),
   'sub_000000000000000000000000p7', 'active', 'pri_year0000000000000000000000', 'b0000000-0000-0000-0000-000000000007', now() + interval '365 days'));
update auth.users set email_confirmed_at = now() where id = 'b0000000-0000-0000-0000-000000000007';
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name = 'paid7';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'ignored', name)
  from deliveries where name in ('refund_partial', 'refund_pending');
reset role;
select pg_temp.assert_true((select status = 'approved' and expires_at > now() + interval '300 days'
  from pg_temp.member('b0000000-0000-0000-0000-000000000007')), 'paid yearly');
set role anon;
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'refunded', 'a full refund applies')
  from deliveries where name = 'refund_full';
select pg_temp.fails(format('select public.dervo_billing_ingest(%L, %L)', body, pg_temp.sig(body, pg_temp.now_s())), '55000')
  from deliveries where name = 'refund_unknown';
reset role;
select pg_temp.assert_true((select a->>'allowed' = 'false' from pg_temp.access('b0000000-0000-0000-0000-000000000007') a), 'a refund ends access now');
-- Paddle keeps a refunded subscription active, so its next update (a card change, say) must not bring access back.
insert into deliveries values
 ('after_refund', jsonb_set(pg_temp.event('evt_000000000000000000000000c5', 'subscription.updated', now() + interval '30 seconds',
   'sub_000000000000000000000000p7', 'active', 'pri_year0000000000000000000000', null, now() + interval '365 days')::jsonb,
   '{data,current_billing_period,starts_at}', to_jsonb(now() - interval '1 day'))::text),
 ('old_unknown', jsonb_set(pg_temp.adjustment('evt_000000000000000000000000c6', 'sub_00000000000000000000000zzy', 'refund', 'full', 'approved')::jsonb,
   '{occurred_at}', to_jsonb(now() - interval '4 days'))::text);
set role anon;
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'applied', 'the update is recorded')
  from deliveries where name = 'after_refund';
select pg_temp.assert_true(public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s()))->>'state' = 'unknown_subscription',
  'an unknown refund stops retrying after 3 days') from deliveries where name = 'old_unknown';
reset role;
select pg_temp.assert_true((select status = 'canceled' and refunded_at is not null from dervo_private.subscriptions
  where subscription_id = 'sub_000000000000000000000000p7'), 'a later update doesn''t undo a refund');
select pg_temp.assert_true((select a->>'allowed' = 'false' from pg_temp.access('b0000000-0000-0000-0000-000000000007') a), 'and access stays ended');

-- Checkout, one at a time per account: a double click reopens the same Paddle checkout; another interval replaces it.
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-000000000002');
select pg_temp.fails($$select public.dervo_billing_checkout_begin('week', repeat('a', 64))$$, '22023');
select pg_temp.fails($$select public.dervo_billing_checkout_begin('month', 'not-a-hash')$$, '22023');
select pg_temp.fails($$select public.dervo_billing_checkout_finish('txn_000000000000000000000000t1', 'https://trydervo.com/checkout')$$, '55000');
select pg_temp.assert_true(public.dervo_billing_checkout_begin('month', repeat('2', 64)) = '{"state": "claimed", "cancel": null}'::jsonb, 'the first click claims');
select pg_temp.assert_true(public.dervo_billing_checkout_begin('month', repeat('2', 64))->>'state' = 'busy', 'a second click while it''s created waits');
select public.dervo_billing_checkout_finish('txn_000000000000000000000000t1', 'https://trydervo.com/checkout?_ptxn=txn_000000000000000000000000t1');
select pg_temp.assert_true(public.dervo_billing_checkout_begin('month', repeat('2', 64)) = jsonb_build_object('state', 'reuse',
  'transaction_id', 'txn_000000000000000000000000t1', 'url', 'https://trydervo.com/checkout?_ptxn=txn_000000000000000000000000t1'), 'a double click reopens the same checkout');
select pg_temp.assert_true(public.dervo_billing_checkout_begin('year', repeat('2', 64)) = '{"state": "claimed", "cancel": "txn_000000000000000000000000t1"}'::jsonb,
  'another interval cancels the open checkout first');
select public.dervo_billing_checkout_release();
select pg_temp.assert_true(public.dervo_billing_checkout_begin('year', repeat('2', 64))->>'cancel' = 'txn_000000000000000000000000t1', 'a failed attempt keeps the open checkout to cancel');
select public.dervo_billing_checkout_release();
reset role;
update dervo_private.checkouts set claimed_at = now() - interval '3 minutes', claim_interval = 'month' where user_id = 'b0000000-0000-0000-0000-000000000002';
set role authenticated;
select pg_temp.assert_true(public.dervo_billing_checkout_begin('month', repeat('2', 64))->>'state' = 'reuse', 'an abandoned claim expires');
reset role;

-- Nobody pays twice: an open subscription blocks checkout. A Paddle trial for an account that already had a trial
-- gives nothing until it's paid. A price change keeps old subscribers renewing.
update dervo_private.billing_settings set trial_mode = 'card', month_trial_price_id = 'pri_mtrial00000000000000000000',
  year_trial_price_id = 'pri_ytrial00000000000000000000';
insert into deliveries values
 ('second_trial', pg_temp.event('evt_000000000000000000000000d1', 'subscription.created', now() + interval '20 seconds',
   'sub_000000000000000000000000p8', 'trialing', 'pri_mtrial00000000000000000000', 'b0000000-0000-0000-0000-000000000007', now() + interval '7 days')),
 ('first_trial', pg_temp.event('evt_000000000000000000000000d2', 'subscription.created', now(),
   'sub_000000000000000000000000p9', 'trialing', 'pri_ytrial00000000000000000000', 'b0000000-0000-0000-0000-0000000000c0', now() + interval '7 days')),
 ('reused_email', pg_temp.event('evt_000000000000000000000000d3', 'subscription.created', now(),
   'sub_00000000000000000000000p10', 'trialing', 'pri_ytrial00000000000000000000', 'b0000000-0000-0000-0000-000000000003', now() + interval '7 days')),
 ('reused_mac', pg_temp.event('evt_000000000000000000000000d4', 'subscription.created', now() + interval '1 second',
   'sub_00000000000000000000000p11', 'trialing', 'pri_mtrial00000000000000000000', 'b0000000-0000-0000-0000-0000000000c1', now() + interval '7 days'));
insert into auth.users(id, email) values ('b0000000-0000-0000-0000-0000000000c0', 'fresh@example.test'),
 ('b0000000-0000-0000-0000-0000000000c1', 'another@example.test'), ('b0000000-0000-0000-0000-0000000000c2', 'Fresh+again@example.test');
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c0');
select pg_temp.assert_true((public.dervo_plan()->'trial'->>'available')::boolean and public.dervo_plan()->'trial'->>'mode' = 'card'
  and (public.dervo_billing_context(repeat('9', 64))->>'trial_eligible')::boolean, 'a fresh account on a fresh Mac gets the card trial');
select pg_temp.assert_true(not (public.dervo_billing_context()->>'trial_eligible')::boolean, 'but not without saying which Mac');
select pg_temp.assert_true(public.dervo_billing_checkout_begin('year', repeat('9', 64))->>'state' = 'claimed', 'checkout records the Mac');
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c1');
select public.dervo_billing_checkout_begin('month', repeat('9', 64));
select pg_temp.as_user('b0000000-0000-0000-0000-000000000003');
select pg_temp.assert_true(not (public.dervo_plan()->'trial'->>'available')::boolean
  and not (public.dervo_billing_context(repeat('3', 64))->>'trial_eligible')::boolean, 'an email that had the app trial gets no card trial');
select pg_temp.as_user('b0000000-0000-0000-0000-000000000007');
select pg_temp.assert_true(not (public.dervo_billing_context()->>'trial_eligible')::boolean, 'a past payer gets no trial price');
reset role;
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries
  where name in ('second_trial', 'first_trial', 'reused_email', 'reused_mac');
reset role;
select pg_temp.assert_true((select a->>'allowed' = 'false' from pg_temp.access('b0000000-0000-0000-0000-000000000007') a), 'a second trial gives nothing');
select pg_temp.assert_true((select a->>'allowed' = 'false' from pg_temp.access('b0000000-0000-0000-0000-000000000003') a), 'a trial on a used email gives nothing');
select pg_temp.assert_true((select a->>'allowed' = 'false' from pg_temp.access('b0000000-0000-0000-0000-0000000000c1') a), 'a trial on a used Mac gives nothing');
select pg_temp.assert_true((select status = 'approved' and expires_at > now() + interval '8 days'
  from pg_temp.member('b0000000-0000-0000-0000-0000000000c0')), 'a first Paddle trial gives access to its end, plus grace');
select pg_temp.assert_true((select count(*) = 2 from dervo_private.trial_identities where user_id = 'b0000000-0000-0000-0000-0000000000c0'),
  'the first card trial records its email and Mac');
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c2');
select pg_temp.assert_true(not (public.dervo_billing_context(repeat('8', 64))->>'trial_eligible')::boolean, 'the same email with a +tag gets no second trial');
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c1');
select pg_temp.assert_true(not (public.dervo_billing_context(repeat('9', 64))->>'trial_eligible')::boolean, 'another email on the same Mac gets no second trial');
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c0');
select pg_temp.assert_true(public.dervo_plan()->>'plan' = 'trial' and not (public.dervo_plan()->>'can_buy')::boolean
  and (public.dervo_plan()->>'can_manage')::boolean, 'a Paddle trial reads as a trial, managed, not bought again');
select pg_temp.assert_true((public.dervo_billing_context()->>'open_subscription')::boolean, 'checkout sees the open subscription');
reset role;
update dervo_private.billing_settings set year_price_id = 'pri_ynew0000000000000000000000';
select pg_temp.assert_true(exists(select 1 from dervo_private.billing_prices where price_id = 'pri_year0000000000000000000000'), 'old prices still grant renewals');

-- A billing period that starts after a refund is a real renewal, so it gives access again.
insert into deliveries values
 ('renewed', jsonb_set(pg_temp.event('evt_000000000000000000000000c7', 'subscription.updated', now() + interval '1 minute',
   'sub_000000000000000000000000p7', 'active', 'pri_year0000000000000000000000', null, now() + interval '400 days')::jsonb,
   '{data,current_billing_period,starts_at}', to_jsonb(now() + interval '1 minute'))::text);
set role anon;
select public.dervo_billing_ingest(body, pg_temp.sig(body, pg_temp.now_s())) from deliveries where name = 'renewed';
reset role;
select pg_temp.assert_true((select status = 'active' and refunded_at is null from dervo_private.subscriptions
  where subscription_id = 'sub_000000000000000000000000p7'), 'a renewal after a refund counts');

-- Going live: sandbox subscriptions stop counting.
update dervo_private.billing_settings set environment = 'live';
set role authenticated;
select pg_temp.as_user('b0000000-0000-0000-0000-0000000000c0');
select pg_temp.assert_true(public.dervo_plan()->'subscription' = 'null'::jsonb and public.dervo_billing_context()->>'customer_id' is null,
  'no sandbox customer reaches the live API');
reset role;
update dervo_private.billing_settings set environment = 'sandbox';

update dervo_private.founder_window set closed_at = now() - interval '1 second';
select pg_temp.as_user('');
