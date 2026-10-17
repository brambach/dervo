-- Error reports (migrations/202609190004_dervo_error_reports.sql), on an isolated PostgreSQL cluster with Supabase-shaped
-- test schemas. Checks who may write and read, that no field takes free text, and what the admin function returns.
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
 ('50000000-0000-0000-0000-000000000001', 'errors-admin@example.test'),
 ('50000000-0000-0000-0000-000000000002', 'errors-maya@example.test'),
 ('50000000-0000-0000-0000-000000000003', 'errors-jordan@example.test');
insert into dervo_private.admins(user_id) values ('50000000-0000-0000-0000-000000000001');
-- Rows need approved beta access (migrations/202609230001): both testers are approved. The admin never adds rows here.
update public.dervo_memberships set status = 'approved', expires_at = null
 where user_id in ('50000000-0000-0000-0000-000000000002', '50000000-0000-0000-0000-000000000003');

select pg_temp.assert_true((select relrowsecurity from pg_class where oid = 'public.dervo_error_reports'::regclass), 'RLS on');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_error_reports', 'select'), 'no select for users');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_error_reports', 'update'), 'no update for users');
select pg_temp.assert_true(not has_table_privilege('authenticated', 'public.dervo_error_reports', 'delete'), 'no delete for users');
select pg_temp.assert_true(not has_column_privilege('authenticated', 'public.dervo_error_reports', 'user_id', 'insert'), 'user_id is never the client''s');
select pg_temp.assert_true(not has_column_privilege('authenticated', 'public.dervo_error_reports', 'created_at', 'insert'), 'created_at is the server''s');
select pg_temp.assert_true(not has_table_privilege('anon', 'public.dervo_error_reports', 'insert'), 'anon adds nothing');
select pg_temp.assert_true((select prosecdef and proconfig @> array['search_path=""'] from pg_proc where proname = 'dervo_admin_errors'), 'definer rights and empty search_path');
create role dervo_errors_nobody nologin;
select pg_temp.assert_true(not has_function_privilege('dervo_errors_nobody', 'public.dervo_admin_errors(integer)', 'execute'), 'public has no execute');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_errors(integer)', 'execute'), 'anon has no execute');

set role anon;
select pg_temp.denied($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000001', 'host', 'Rejected')$q$);
select pg_temp.denied('select public.dervo_admin_errors()');
reset role;

set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000002', false);
insert into public.dervo_error_reports(install_id, source, error_type, file, function, line, operation, provider, app_version, count) values
 ('60000000-0000-0000-0000-000000000002', 'host', 'UnboundLocalError', 'conversation_coding.py', 'ConversationCoding.permission', 224, 'coding_permission', null, '0.1.0-beta.10', 3),
 ('60000000-0000-0000-0000-000000000002', 'task', 'KeyError', 'claude_coding.py', 'ClaudeCodingTurn.tick', 790, 'coding_turn', 'claude', '0.1.0-beta.10', 1),
 ('60000000-0000-0000-0000-000000000002', 'host', 'Rejected', 'beta_update_host.py', 'Updates.dispatch.<locals>.work', 240, '_beta_update_download', null, '0.1.0-beta.10', 1);
insert into public.dervo_error_reports(install_id, source, error_type, operation) values
 ('60000000-0000-0000-0000-000000000002', 'renderer', 'TypeError', 'settings.model');
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000003', false);
insert into public.dervo_error_reports(install_id, source, error_type, file, function, line, operation, app_version, count) values
 ('60000000-0000-0000-0000-000000000003', 'host', 'UnboundLocalError', 'conversation_coding.py', 'ConversationCoding.permission', 224, 'coding_permission', '0.1.0-beta.11', 2);

-- Naming a user_id is refused, and nobody reads, changes or removes rows, their own included.
select pg_temp.denied($q$insert into public.dervo_error_reports(user_id, install_id, source, error_type) values ('50000000-0000-0000-0000-000000000002', '60000000-0000-0000-0000-000000000003', 'host', 'Rejected')$q$);
select pg_temp.denied('select count(*) from public.dervo_error_reports');
select pg_temp.denied($q$update public.dervo_error_reports set count = 1$q$);
select pg_temp.denied('delete from public.dervo_error_reports');

-- No free text in any field: a message, a path, an email, a sentence, a newline or an oversized count is refused.
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000003', 'host', 'No such file: /Users/maya/secret-project')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000003', 'host', E'KeyError\nmaya')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000003', 'plugin', 'KeyError')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file, function, line) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', '/Users/maya/app/host.py', 'dispatch', 1)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file, function, line) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'secret_plan.txt', 'dispatch', 1)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file, function, line) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'host.py', 'maya@example.test', 1)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file, function, line) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'host.py', 'open the billing project', 1)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file, function, line) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'host.py', 'dispatch', 0)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, file) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'host.py')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, operation) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'Fix login in billing')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, provider) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'openrouter')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, app_version) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 'beta 9; drop table')$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, count) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 10001)$q$);
select pg_temp.refused($q$insert into public.dervo_error_reports(install_id, source, error_type, count) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError', 0)$q$);
reset role;

-- Only while beta access is approved and unexpired (security review L2, 23 Sep): an account that's still pending, one
-- whose access expired and one that was revoked add nothing. Approved again, the tester's rows are accepted again.
insert into auth.users(id, email) values ('50000000-0000-0000-0000-000000000004', 'pending@example.test');
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000004', false);
select pg_temp.denied($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000004', 'host', 'KeyError')$q$);
reset role;
update public.dervo_memberships set expires_at = now() - interval '1 minute' where user_id = '50000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000003', false);
select pg_temp.denied($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError')$q$);
reset role;
update public.dervo_memberships set status = 'revoked', expires_at = null where user_id = '50000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000003', false);
select pg_temp.denied($q$insert into public.dervo_error_reports(install_id, source, error_type) values ('60000000-0000-0000-0000-000000000003', 'host', 'KeyError')$q$);
reset role;
update public.dervo_memberships set status = 'approved', expires_at = null where user_id = '50000000-0000-0000-0000-000000000003';

select pg_temp.assert_true((select count(*) = 5 from public.dervo_error_reports), 'every accepted row was kept');
select pg_temp.assert_true((select count(*) = 4 from public.dervo_error_reports where user_id = '50000000-0000-0000-0000-000000000002'), 'rows belong to the token''s user');

-- A tester who isn't an admin, and a missing identity, are refused before anything is read.
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000002', false);
select pg_temp.denied('select public.dervo_admin_errors()');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied('select public.dervo_admin_errors()');

-- An admin reads one group per kind and place, with the counts added up and the testers counted once each.
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000001', false);
create temporary table error_report as select public.dervo_admin_errors() as doc;
select pg_temp.assert_true((select (doc->>'days')::int = 7 and jsonb_array_length(doc->'errors') = 4 from error_report), 'four groups');
select pg_temp.assert_true((select (e->>'count')::int = 5 and (e->>'users')::int = 2 and e->>'line' = '224' and e->>'app_version' = '0.1.0-beta.11'
  from error_report, jsonb_array_elements(doc->'errors') e where e->>'error_type' = 'UnboundLocalError'), 'counts summed, testers counted, newest version');
select pg_temp.assert_true((select e->>'operation' = 'settings.model' and e->'file' = 'null'::jsonb
  from error_report, jsonb_array_elements(doc->'errors') e where e->>'source' = 'renderer'), 'renderer rows have a screen and no location');
select pg_temp.assert_true((select position('@' in doc::text) = 0 from error_report), 'no email in the report');
select pg_temp.assert_true((select (public.dervo_admin_errors(0)->>'days')::int = 1 and (public.dervo_admin_errors(5000)->>'days')::int = 90), 'window clamped to 1..90 days');
reset role;

update public.dervo_error_reports set created_at = now() - interval '30 days' where user_id = '50000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true((select (e->>'count')::int = 3 from jsonb_array_elements(public.dervo_admin_errors()->'errors') e
  where e->>'error_type' = 'UnboundLocalError'), 'window excludes old rows');
reset role;

delete from dervo_private.admins where user_id = '50000000-0000-0000-0000-000000000001';
set role authenticated;
select set_config('request.jwt.claim.sub', '50000000-0000-0000-0000-000000000001', false);
select pg_temp.denied('select public.dervo_admin_errors()');
reset role;
drop role dervo_errors_nobody;
select 'Dervo error reports policy checks passed' as result;
