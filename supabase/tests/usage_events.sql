-- Usage events (migrations/202609190002_dervo_usage_events.sql), on an isolated PostgreSQL cluster with Supabase-shaped
-- test schemas. Checks who may write and read, and what the admin function returns. Not hosted Auth or PostgREST.
create function pg_temp.assert_true(value boolean, label text) returns void language plpgsql as $$
begin if value is distinct from true then raise exception 'Assertion failed: %', label; end if; end;
$$;
create function pg_temp.denied(statement text) returns void language plpgsql as $$
begin
  begin execute statement;
  exception when insufficient_privilege then return;
  end;
  raise exception 'Expected permission denial: %', statement;
end;
$$;
create function pg_temp.refused(statement text) returns void language plpgsql as $$
begin
  begin execute statement;
  exception when check_violation then return;
  end;
  raise exception 'Expected a check violation: %', statement;
end;
$$;

insert into auth.users(id, email) values
 ('30000000-0000-0000-0000-000000000001', 'usage-admin@example.test'),
 ('30000000-0000-0000-0000-000000000002', 'usage-maya@example.test'),
 ('30000000-0000-0000-0000-000000000003', 'usage-jordan@example.test');
insert into dervo_private.admins(user_id) values ('30000000-0000-0000-0000-000000000001');
-- Rows need approved beta access (migrations/202609230001): both testers are approved. The admin never adds rows here.
update public.dervo_memberships set status = 'approved', expires_at = null
 where user_id in ('30000000-0000-0000-0000-000000000002', '30000000-0000-0000-0000-000000000003');

-- What the table and function are: RLS on, insert-only columns for signed-in users, nothing for anon, definer rights and an
-- empty search_path on the admin function.
select pg_temp.assert_true((select relrowsecurity from pg_class where oid = 'public.dervo_usage_events'::regclass), 'RLS on');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_usage_events', 'select'), 'no select for users');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_usage_events', 'update'), 'no update for users');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_usage_events', 'delete'), 'no delete for users');
select pg_temp.assert_true(not has_column_privilege('authenticated', 'public.dervo_usage_events', 'user_id', 'insert'), 'user_id is never the client''s');
select pg_temp.assert_true(has_column_privilege('authenticated', 'public.dervo_usage_events', 'event', 'insert'), 'users may add events');
select pg_temp.assert_true(not has_table_privilege('anon', 'public.dervo_usage_events', 'insert'), 'anon adds nothing');
select pg_temp.assert_true((select prosecdef and proconfig @> array['search_path=""'] from pg_proc where proname = 'dervo_admin_usage'), 'definer rights and empty search_path');
create role dervo_usage_nobody nologin;
select pg_temp.assert_true(not has_function_privilege('dervo_usage_nobody', 'public.dervo_admin_usage(integer)', 'execute'), 'public has no execute');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_usage(integer)', 'execute'), 'anon has no execute');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.dervo_admin_usage(integer)', 'execute'), 'signed-in users may call it');
select pg_temp.assert_true((select count(*) = 1 from pg_indexes where tablename = 'dervo_usage_events' and indexdef like '%(created_at, event)%'), 'time and event index');

set role anon;
select pg_temp.denied($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000001', 'app_opened')$q$);
select pg_temp.denied('select public.dervo_admin_usage()');
reset role;

set role authenticated;
-- A tester adds rows as themselves. The server fills user_id from the token.
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000002', false);
insert into public.dervo_usage_events(install_id, event, properties, app_version) values
 ('40000000-0000-0000-0000-000000000002', 'app_opened', '{}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'screen_viewed', '{"screen":"home"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'screen_viewed', '{"screen":"thread"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'screen_viewed', '{"screen":"thread"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'first_run_step', '{"step":"sign_in","status":"reached"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'first_run_step', '{"step":"sign_in","status":"completed"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'first_run_step', '{"step":"folder","status":"reached"}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"claude","outcome":"completed","seconds":40}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"claude","outcome":"completed","seconds":80}', '0.1.0-beta.9'),
 ('40000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"codex","outcome":"failed","error_code":"usage_limit"}', '0.1.0-beta.9');
-- A second tester, and a malformed duration that must not break the admin's report.
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000003', false);
insert into public.dervo_usage_events(install_id, event, properties) values
 ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '{"screen":"thread"}'),
 ('40000000-0000-0000-0000-000000000003', 'first_run_step', '{"step":"sign_in","status":"reached"}'),
 ('40000000-0000-0000-0000-000000000003', 'task_ended', '{"provider":"claude","outcome":"stopped","seconds":"soon"}'),
 ('40000000-0000-0000-0000-000000000003', 'task_ended', '{"provider":"openrouter","outcome":"completed"}'),
 ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '{"screen":"My Secret Screen"}');

-- Naming a user_id, anyone's including your own, is refused: the column isn't the client's to set.
select pg_temp.denied($q$insert into public.dervo_usage_events(user_id, install_id, event) values ('30000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000003', 'app_opened')$q$);
select pg_temp.denied($q$insert into public.dervo_usage_events(user_id, install_id, event) values ('30000000-0000-0000-0000-000000000003', '40000000-0000-0000-0000-000000000003', 'app_opened')$q$);
-- No identity, no row (user_id can't be null), and nobody reads, changes or removes rows, their own included.
select set_config('request.jwt.claim.sub', '', false);
do $$ begin
  insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', 'app_opened');
  raise exception 'Expected a missing identity to be refused';
exception when not_null_violation or insufficient_privilege then null;
end $$;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000003', false);
select pg_temp.denied('select count(*) from public.dervo_usage_events');
select pg_temp.denied($q$update public.dervo_usage_events set event = 'app_opened'$q$);
select pg_temp.denied('delete from public.dervo_usage_events');

-- The server's own limits: a short lowercase event name, a flat object of short values, a bounded size, a version shape.
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', 'x')$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', 'Opened my secret project')$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', repeat('a', 41))$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '["home"]')$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '{"prompt":{"text":"hi"}}')$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '{"path":["a","b"]}')$q$);
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', '{"prompt":"Fix the login bug in the billing service please"}')$q$);
select pg_temp.refused(format($q$insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', %L)$q$,
  (select jsonb_object_agg('k' || n, 'value-' || n) from generate_series(1, 40) n)::text));
select pg_temp.refused($q$insert into public.dervo_usage_events(install_id, event, app_version) values ('40000000-0000-0000-0000-000000000003', 'app_opened', 'beta 9; drop table')$q$);
-- The limits let ordinary events through: 40 characters is the longest string value.
insert into public.dervo_usage_events(install_id, event, properties) values ('40000000-0000-0000-0000-000000000003', 'screen_viewed', jsonb_build_object('screen', repeat('s', 40)));
reset role;

-- Only while beta access is approved and unexpired (security review L2, 23 Sep): an account that's still pending, one
-- whose access expired and one that was revoked add nothing. Approved again, the tester's rows are accepted again.
insert into auth.users(id, email) values ('30000000-0000-0000-0000-000000000004', 'pending@example.test');
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000004', false);
select pg_temp.denied($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000004', 'app_opened')$q$);
reset role;
update public.dervo_memberships set expires_at = now() - interval '1 minute' where user_id = '30000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000003', false);
select pg_temp.denied($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', 'app_opened')$q$);
reset role;
update public.dervo_memberships set status = 'revoked', expires_at = null where user_id = '30000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000003', false);
select pg_temp.denied($q$insert into public.dervo_usage_events(install_id, event) values ('40000000-0000-0000-0000-000000000003', 'app_opened')$q$);
reset role;
update public.dervo_memberships set status = 'approved', expires_at = null where user_id = '30000000-0000-0000-0000-000000000003';

select pg_temp.assert_true((select count(*) = 16 from public.dervo_usage_events), 'every accepted row was kept');
select pg_temp.assert_true((select count(*) = 10 from public.dervo_usage_events where user_id = '30000000-0000-0000-0000-000000000002'), 'rows belong to the token''s user');
select pg_temp.assert_true((select count(*) = 6 from public.dervo_usage_events where user_id = '30000000-0000-0000-0000-000000000003'), 'second tester''s rows are theirs');

-- The policy holds on its own: with the column granted by mistake, a row naming someone else is still refused.
grant insert (user_id) on public.dervo_usage_events to authenticated;
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000003', false);
select pg_temp.denied($q$insert into public.dervo_usage_events(user_id, install_id, event) values ('30000000-0000-0000-0000-000000000002', '40000000-0000-0000-0000-000000000003', 'app_opened')$q$);
reset role;
revoke insert (user_id) on public.dervo_usage_events from authenticated;

-- A signed-in tester who isn't an admin, and a missing identity, are refused before anything is read.
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000002', false);
select pg_temp.denied('select public.dervo_admin_usage()');
select pg_temp.denied('select public.dervo_admin_usage(90)');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied('select public.dervo_admin_usage()');

-- An admin reads counts: active testers, daily totals, screens, the funnel in order, and task outcomes by provider.
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000001', false);
create temporary table usage_report as select public.dervo_admin_usage() as doc;
select pg_temp.assert_true((select (doc->>'days')::int = 7 and (doc->>'active')::int = 2 from usage_report), 'two active testers in 7 days');
select pg_temp.assert_true((select jsonb_array_length(doc->'daily') = 1 and (doc->'daily'->0->>'users')::int = 2
  and (doc->'daily'->0->>'events')::int = 16 from usage_report), 'one day of activity');
select pg_temp.assert_true((select (s->>'count')::int = 3 and (s->>'users')::int = 2 from usage_report, jsonb_array_elements(doc->'screens') s
  where s->>'screen' = 'thread'), 'thread views and viewers');
select pg_temp.assert_true((select (e->>'count')::int = 6 from usage_report, jsonb_array_elements(doc->'events') e
  where e->>'event' = 'screen_viewed'), 'per-event counts');
select pg_temp.assert_true((select count(*) = 0 from usage_report, jsonb_array_elements(doc->'screens') s where s->>'screen' like '%Secret%'), 'only screen-shaped names');
select pg_temp.assert_true((select array_agg(f->>'step' order by n) = array['sign_in', 'pending', 'folder', 'start', 'project_created']
  from usage_report, jsonb_array_elements(doc->'funnel') with ordinality as x(f, n)), 'funnel steps in order');
select pg_temp.assert_true((select (f->>'reached')::int = 2 and (f->>'completed')::int = 1 from usage_report, jsonb_array_elements(doc->'funnel') f
  where f->>'step' = 'sign_in'), 'funnel counts people, not rows');
select pg_temp.assert_true((select (f->>'reached')::int = 0 from usage_report, jsonb_array_elements(doc->'funnel') f
  where f->>'step' = 'project_created'), 'empty steps count zero');
select pg_temp.assert_true((select (t->>'count')::int = 2 and (t->>'median_seconds')::int = 60 from usage_report, jsonb_array_elements(doc->'tasks') t
  where t->>'provider' = 'claude' and t->>'outcome' = 'completed'), 'median task time');
select pg_temp.assert_true((select (t->>'count')::int = 1 and t->'median_seconds' = 'null'::jsonb from usage_report, jsonb_array_elements(doc->'tasks') t
  where t->>'provider' = 'claude' and t->>'outcome' = 'stopped'), 'a malformed duration is left out, not an error');
select pg_temp.assert_true((select count(*) = 0 from usage_report, jsonb_array_elements(doc->'tasks') t where t->>'provider' = 'openrouter'), 'only known providers');
select pg_temp.assert_true((select (public.dervo_admin_usage(0)->>'days')::int = 1 and (public.dervo_admin_usage(5000)->>'days')::int = 90), 'window clamped to 1..90 days');
select pg_temp.assert_true((select position('@' in doc::text) = 0 from usage_report), 'no email in the report');
reset role;

-- Old rows fall out of the window, but still count toward the funnel.
update public.dervo_usage_events set created_at = now() - interval '30 days' where user_id = '30000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true((select (public.dervo_admin_usage()->>'active')::int = 1), 'window excludes old rows');
select pg_temp.assert_true((select (f->>'reached')::int = 2 from jsonb_array_elements(public.dervo_admin_usage()->'funnel') f where f->>'step' = 'sign_in'), 'funnel is all time');
reset role;

-- Removing the admin record takes effect at once.
delete from dervo_private.admins where user_id = '30000000-0000-0000-0000-000000000001';
set role authenticated;
select set_config('request.jwt.claim.sub', '30000000-0000-0000-0000-000000000001', false);
select pg_temp.denied('select public.dervo_admin_usage()');
reset role;
drop role dervo_usage_nobody;
select 'Dervo usage events policy checks passed' as result;
