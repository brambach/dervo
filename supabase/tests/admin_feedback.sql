-- Tester reports for admins (migrations/202609250001_dervo_admin_feedback.sql), on an isolated PostgreSQL cluster with
-- Supabase-shaped test schemas. A tester and a missing identity get nothing; an admin gets the reports, newest first,
-- with a first name and no email, user id or local ids.
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

insert into auth.users(id, email) values
 ('70000000-0000-0000-0000-000000000001', 'reports-admin@example.test'),
 ('70000000-0000-0000-0000-000000000002', 'harrison@example.test'),
 ('70000000-0000-0000-0000-000000000003', 'nameless@example.test');
insert into dervo_private.admins(user_id) values ('70000000-0000-0000-0000-000000000001');
insert into public.dervo_profiles(user_id, display_name) values ('70000000-0000-0000-0000-000000000002', ' Harrison Ford ')
  on conflict (user_id) do update set display_name = excluded.display_name;
insert into dervo_private.beta_feedback(user_id, id, category, message, context, created_at) values
 ('70000000-0000-0000-0000-000000000002', '71000000-0000-0000-0000-000000000001', 'bug',
  'The update failed. Dervo was still in Downloads.',
  '{"route":"settings","appVersion":"0.1.0-beta.15","projectId":"secret-project","conversationId":"c1","provider":"claude"}', now() - interval '2 hours'),
 ('70000000-0000-0000-0000-000000000002', '71000000-0000-0000-0000-000000000002', 'idea',
  'Show me what gets saved.', '{"route":"knowledge","appVersion":"0.1.0-beta.14"}', now() - interval '2 days'),
 ('70000000-0000-0000-0000-000000000003', '71000000-0000-0000-0000-000000000003', 'confusing',
  'I couldn''t tell where my answer went.', '{"route":"conversation","appVersion":"0.1.0-beta.15"}', now() - interval '1 day'),
 ('70000000-0000-0000-0000-000000000003', '71000000-0000-0000-0000-000000000004', 'bug',
  'Too old to show.', '{"route":"workspace"}', now() - interval '40 days');

select pg_temp.assert_true((select prosecdef and proconfig @> array['search_path=""'] from pg_proc where proname = 'dervo_admin_feedback'), 'definer rights and empty search_path');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_feedback(integer)', 'execute'), 'anon has no execute');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'dervo_private.beta_feedback', 'select'), 'testers still read no feedback rows');

set role anon;
select pg_temp.denied('select public.dervo_admin_feedback()');
reset role;

-- A tester who isn't an admin, and a missing identity, are refused before anything is read.
set role authenticated;
select set_config('request.jwt.claim.sub', '70000000-0000-0000-0000-000000000002', false);
select pg_temp.denied('select public.dervo_admin_feedback()');
select pg_temp.denied('select count(*) from dervo_private.beta_feedback');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied('select public.dervo_admin_feedback()');

-- The admin reads the last 30 days, newest first, with a first name only.
select set_config('request.jwt.claim.sub', '70000000-0000-0000-0000-000000000001', false);
create temporary table feedback_report as select public.dervo_admin_feedback() as doc;
-- Earlier checks on this cluster add feedback of their own, so these look at this file's rows.
create temporary table mine as select e.r, e.ordinality from feedback_report, jsonb_array_elements(doc->'reports') with ordinality e(r, ordinality)
  where e.r->>'id' like '71000000-%';
select pg_temp.assert_true((select (doc->>'days')::int = 30 from feedback_report), 'thirty days by default');
select pg_temp.assert_true((select count(*) = 3 from mine), 'three recent reports');
select pg_temp.assert_true((select array_agg(r->>'id' order by ordinality) = array['71000000-0000-0000-0000-000000000001',
  '71000000-0000-0000-0000-000000000003', '71000000-0000-0000-0000-000000000002'] from mine), 'newest first');
select pg_temp.assert_true((select r->>'tester' = 'Harrison' and r->>'app_version' = '0.1.0-beta.15' and r->>'route' = 'settings' and r->>'provider' = 'claude'
  from mine where r->>'category' = 'bug'), 'first name, build, screen and provider');
select pg_temp.assert_true((select r->'tester' = 'null'::jsonb from mine where r->>'category' = 'confusing'), 'no name set reads as null');
select pg_temp.assert_true((select position('example.test' in doc::text) = 0 and position('secret-project' in doc::text) = 0
  and position('70000000' in doc::text) = 0 and position('"c1"' in doc::text) = 0 from feedback_report), 'no email, user id or local ids');
select pg_temp.assert_true((select count(*) = 4 from jsonb_array_elements(public.dervo_admin_feedback(90)->'reports') r
  where r->>'id' like '71000000-%'), 'a longer window finds the older report');
select pg_temp.assert_true((select (public.dervo_admin_feedback(1000)->>'days')::int = 90), 'the window is capped at 90 days');
reset role;
select 'PASS: tester reports for admins only, newest first, first names and no identifiers';
