-- Founder testers (migrations/202609240002_dervo_founders.sql), on an isolated PostgreSQL cluster with Supabase-shaped
-- test schemas. Checks who becomes a founder, that it stays, and who may ask, not hosted Auth or PostgREST.
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
create function pg_temp.founder(member text) returns boolean language sql as $$
  select exists(select 1 from dervo_private.founders where user_id = member::uuid)
$$;

-- What the functions are: definer rights, an empty search_path, and only signed-in users may ask.
select pg_temp.assert_true((select count(*) = 2 and bool_and(prosecdef and proconfig @> array['search_path=""'])
  from pg_proc where proname in ('dervo_is_founder', 'record_founder')), 'definer rights and empty search_path');
create role dervo_founders_nobody nologin;
select pg_temp.assert_true(not has_function_privilege('dervo_founders_nobody', 'public.dervo_is_founder()', 'execute'), 'public has no execute');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_is_founder()', 'execute'), 'anon has no execute');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.dervo_is_founder()', 'execute'), 'signed-in users may ask');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'dervo_private.record_founder()', 'execute'), 'nobody calls the trigger function');
select pg_temp.assert_true((select closed_at is null from dervo_private.founder_window), 'the window starts open');
-- dervo_access() keeps its five fields, because older apps refuse an answer with more.
select pg_temp.assert_true((select pg_get_functiondef('public.dervo_access()'::regprocedure) not like '%founder%'), 'dervo_access unchanged');

insert into auth.users(id, email) values
 ('f0000000-0000-0000-0000-000000000001', 'early@example.test'),
 ('f0000000-0000-0000-0000-000000000002', 'waiting@example.test'),
 ('f0000000-0000-0000-0000-000000000003', 'late@example.test'),
 ('f0000000-0000-0000-0000-000000000004', 'invited@example.test');

-- A new sign-up is pending, so not a founder. Approval while the window is open makes one, and so does any later path
-- to approved (here a direct update, as dervo_set_membership and invitations do).
select pg_temp.assert_true(not pg_temp.founder('f0000000-0000-0000-0000-000000000001'), 'pending is not a founder');
update public.dervo_memberships set status = 'approved' where user_id = 'f0000000-0000-0000-0000-000000000001';
select pg_temp.assert_true(pg_temp.founder('f0000000-0000-0000-0000-000000000001'), 'approved before launch is a founder');
update public.dervo_memberships set status = 'approved', expires_at = now() + interval '14 days'
  where user_id = 'f0000000-0000-0000-0000-000000000004';
select pg_temp.assert_true(pg_temp.founder('f0000000-0000-0000-0000-000000000004'), 'an approval with an end date counts too');

-- It stays: revoking, expiring or approving again leaves one row with its first grant time.
create temporary table founders_before as select granted_at from dervo_private.founders
  where user_id = 'f0000000-0000-0000-0000-000000000001';
update public.dervo_memberships set status = 'revoked' where user_id = 'f0000000-0000-0000-0000-000000000001';
update public.dervo_memberships set status = 'approved' where user_id = 'f0000000-0000-0000-0000-000000000001';
update public.dervo_memberships set expires_at = now() - interval '1 day' where user_id = 'f0000000-0000-0000-0000-000000000004';
select pg_temp.assert_true((select count(*) = 1 and min(granted_at) = (select granted_at from founders_before)
  from dervo_private.founders where user_id = 'f0000000-0000-0000-0000-000000000001'), 'one row, first grant kept');
select pg_temp.assert_true(pg_temp.founder('f0000000-0000-0000-0000-000000000004'), 'an expired membership keeps it');

-- After the public launch the window closes, and a new approval isn't a founder.
update dervo_private.founder_window set closed_at = now() - interval '1 second';
update public.dervo_memberships set status = 'approved' where user_id = 'f0000000-0000-0000-0000-000000000003';
select pg_temp.assert_true(not pg_temp.founder('f0000000-0000-0000-0000-000000000003'), 'approved after launch is not a founder');
select pg_temp.assert_true(pg_temp.founder('f0000000-0000-0000-0000-000000000001'), 'closing the window takes nothing away');

-- Asking: each person learns only about themselves, and nobody reads or writes the tables.
set role anon;
select pg_temp.denied('select public.dervo_is_founder()');
reset role;
set role authenticated;
select set_config('request.jwt.claim.sub', 'f0000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true(public.dervo_is_founder(), 'a founder is told yes');
select set_config('request.jwt.claim.sub', 'f0000000-0000-0000-0000-000000000002', false);
select pg_temp.assert_true(not public.dervo_is_founder(), 'a pending tester is told no');
select set_config('request.jwt.claim.sub', 'f0000000-0000-0000-0000-000000000003', false);
select pg_temp.assert_true(not public.dervo_is_founder(), 'an after-launch tester is told no');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.assert_true(not public.dervo_is_founder(), 'no identity is told no');
select pg_temp.denied('select * from dervo_private.founders');
select pg_temp.denied($q$update dervo_private.founder_window set closed_at = null$q$);
select pg_temp.denied($q$insert into dervo_private.founders(user_id) values ('f0000000-0000-0000-0000-000000000002')$q$);
reset role;

-- Leave the cluster as the later checks expect it.
update dervo_private.founder_window set closed_at = null;
drop role dervo_founders_nobody;
select 'Dervo founder checks passed' as result;
