-- Usage per tester (migrations/202609280001_dervo_admin_usage_people.sql), on an isolated PostgreSQL cluster with
-- Supabase-shaped test schemas. Checks who may call it and what it counts per person. Not hosted Auth or PostgREST.
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

-- usage_events.sql ran first and left its rows; start from none so the counts below are this file's own.
truncate public.dervo_usage_events;
insert into auth.users(id, email) values
 ('31000000-0000-0000-0000-000000000001', 'people-admin@example.test'),
 ('31000000-0000-0000-0000-000000000002', 'people-busy@example.test'),
 ('31000000-0000-0000-0000-000000000003', 'people-idle@example.test'),
 ('31000000-0000-0000-0000-000000000004', 'people-lapsed@example.test'),
 ('31000000-0000-0000-0000-000000000005', 'people-waiting@example.test');
insert into dervo_private.admins(user_id) values ('31000000-0000-0000-0000-000000000001');
update public.dervo_memberships set status = 'approved', expires_at = null
 where user_id in ('31000000-0000-0000-0000-000000000001', '31000000-0000-0000-0000-000000000002',
                   '31000000-0000-0000-0000-000000000003', '31000000-0000-0000-0000-000000000004');
-- 005 stays pending: listed, with nothing sent.

select pg_temp.assert_true((select prosecdef and proconfig @> array['search_path=""'] from pg_proc where proname = 'dervo_admin_usage_people'), 'definer rights and empty search_path');
create role dervo_people_nobody nologin;
select pg_temp.assert_true(not has_function_privilege('dervo_people_nobody', 'public.dervo_admin_usage_people(integer)', 'execute'), 'public has no execute');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_usage_people(integer)', 'execute'), 'anon has no execute');

set role authenticated;
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000001', false);
insert into public.dervo_usage_events(install_id, event, properties, app_version) values
 ('41000000-0000-0000-0000-000000000001', 'app_opened', '{}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000001', 'task_started', '{"provider":"claude"}', '0.1.0-beta.16.3');
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000002', false);
insert into public.dervo_usage_events(install_id, event, properties, app_version) values
 ('41000000-0000-0000-0000-000000000002', 'app_opened', '{}', '0.1.0-beta.16.2'),
 ('41000000-0000-0000-0000-000000000002', 'app_opened', '{}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'thread_created', '{}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'home_request', '{"outcome":"completed","threads_created":2}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_started', '{"provider":"claude"}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_started', '{"provider":"codex"}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_started', '{"provider":"codex"}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"claude","outcome":"completed","seconds":40}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"codex","outcome":"failed"}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'task_ended', '{"provider":"codex","outcome":"stopped"}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'knowledge_rule_added', '{}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'lesson_approved', '{}', '0.1.0-beta.16.3'),
 ('41000000-0000-0000-0000-000000000002', 'feedback_opened', '{}', null);
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000004', false);
insert into public.dervo_usage_events(install_id, event, properties, app_version) values
 ('41000000-0000-0000-0000-000000000004', 'app_opened', '{}', '0.1.0-beta.14'),
 ('41000000-0000-0000-0000-000000000004', 'thread_created', '{}', '0.1.0-beta.14');
reset role;
-- The admin was last here two hours ago, the busy tester was also here yesterday, and the lapsed tester last opened
-- Dervo 20 days ago.
update public.dervo_usage_events set created_at = now() - interval '2 hours' where user_id = '31000000-0000-0000-0000-000000000001';
update public.dervo_usage_events set created_at = now() - interval '1 day'
 where user_id = '31000000-0000-0000-0000-000000000002' and app_version = '0.1.0-beta.16.2';
update public.dervo_usage_events set created_at = now() - interval '20 days' where user_id = '31000000-0000-0000-0000-000000000004';

-- Testers and missing identities are refused before anything is read.
set role anon;
select pg_temp.denied('select public.dervo_admin_usage_people()');
reset role;
set role authenticated;
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000002', false);
select pg_temp.denied('select public.dervo_admin_usage_people()');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied('select public.dervo_admin_usage_people()');

select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000001', false);
create temporary table people_report as select public.dervo_admin_usage_people() as doc;
create temporary view people as select p from people_report, jsonb_array_elements(doc->'people') p;
select pg_temp.assert_true((select (doc->>'days')::int = 7 from people_report), 'seven days by default');
-- The busy tester was seen last, then the admin, then the lapsed tester, then the two who never sent anything, by email.
select pg_temp.assert_true((select array_agg(p->>'email' order by n) = array['people-busy@example.test', 'people-admin@example.test',
    'people-lapsed@example.test', 'people-idle@example.test', 'people-waiting@example.test']
  from people_report, jsonb_array_elements(doc->'people') with ordinality as x(p, n)
  where p->>'email' like 'people-%'), 'most recently seen first, never-seen last');
select pg_temp.assert_true((select (p->>'is_admin')::boolean and p->>'status' = 'approved' from people where p->>'email' = 'people-admin@example.test'), 'admin marked');
select pg_temp.assert_true((select (p->>'active_days')::int = 2 and (p->>'opens')::int = 2 and (p->>'threads')::int = 1
    and (p->>'home_requests')::int = 1 and (p->>'tasks_started')::int = 3 and (p->>'tasks_completed')::int = 1
    and (p->>'tasks_failed')::int = 1 and (p->>'claude_tasks')::int = 1 and (p->>'codex_tasks')::int = 2
    and (p->>'knowledge')::int = 2 and (p->>'feedback_opened')::int = 1 and not (p->>'is_admin')::boolean
  from people where p->>'email' = 'people-busy@example.test'), 'a busy tester''s counts');
select pg_temp.assert_true((select p->>'app_version' = '0.1.0-beta.16.3' from people where p->>'email' = 'people-busy@example.test'),
  'latest version that was sent, skipping a row without one');
select pg_temp.assert_true((select p->'last_seen' <> 'null'::jsonb and (p->>'active_days')::int = 0 and (p->>'opens')::int = 0
    and p->>'app_version' = '0.1.0-beta.14'
  from people where p->>'email' = 'people-lapsed@example.test'), 'outside the window: last seen kept, counts zero');
select pg_temp.assert_true((select p->'last_seen' = 'null'::jsonb and p->'app_version' = 'null'::jsonb and (p->>'tasks_started')::int = 0
  from people where p->>'email' = 'people-idle@example.test'), 'approved but never seen');
select pg_temp.assert_true((select p->>'status' = 'pending' from people where p->>'email' = 'people-waiting@example.test'), 'pending listed');
-- Only the fixed fields, nothing from a row's properties beyond the counts.
select pg_temp.assert_true((select bool_and((select array_agg(k order by k) from jsonb_object_keys(p) k) = array['active_days', 'app_version',
    'claude_tasks', 'codex_tasks', 'email', 'feedback_opened', 'home_requests', 'is_admin', 'knowledge', 'last_seen', 'opens', 'status',
    'tasks_completed', 'tasks_failed', 'tasks_started', 'threads', 'user_id']) from people), 'fixed fields per person');
-- A 30-day window brings the lapsed tester's counts back.
select pg_temp.assert_true((select (p->>'opens')::int = 1 and (p->>'threads')::int = 1
  from jsonb_array_elements(public.dervo_admin_usage_people(30)->'people') p where p->>'email' = 'people-lapsed@example.test'), '30 days');
select pg_temp.assert_true((select (public.dervo_admin_usage_people(0)->>'days')::int = 1
  and (public.dervo_admin_usage_people(5000)->>'days')::int = 90), 'window clamped to 1..90 days');
reset role;

-- A revoked tester who sent nothing in the window drops off; one who did still shows.
update public.dervo_memberships set status = 'revoked' where user_id in ('31000000-0000-0000-0000-000000000003', '31000000-0000-0000-0000-000000000002');
set role authenticated;
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true((select count(*) = 0 from jsonb_array_elements(public.dervo_admin_usage_people()->'people') p
  where p->>'email' = 'people-idle@example.test'), 'revoked and idle: gone');
select pg_temp.assert_true((select p->>'status' = 'revoked' from jsonb_array_elements(public.dervo_admin_usage_people()->'people') p
  where p->>'email' = 'people-busy@example.test'), 'revoked but active in the window: shown as revoked');
reset role;

delete from dervo_private.admins where user_id = '31000000-0000-0000-0000-000000000001';
set role authenticated;
select set_config('request.jwt.claim.sub', '31000000-0000-0000-0000-000000000001', false);
select pg_temp.denied('select public.dervo_admin_usage_people()');
reset role;
drop role dervo_people_nobody;
select 'Dervo usage per tester checks passed' as result;
