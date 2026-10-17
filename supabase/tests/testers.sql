-- Testers functions (migrations/202609190001_dervo_testers.sql), on an isolated PostgreSQL cluster with
-- Supabase-shaped test schemas. Checks who may call them and what they return, not hosted Auth or PostgREST.
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

insert into auth.users(id, email, created_at) values
 ('10000000-0000-0000-0000-000000000001', 'admin@example.test', now() - interval '3 days'),
 ('10000000-0000-0000-0000-000000000002', 'maya@example.test', now() - interval '1 day'),
 ('10000000-0000-0000-0000-000000000003', 'jordan@example.test', now() + interval '1 minute');
insert into dervo_private.admins(user_id) values ('10000000-0000-0000-0000-000000000001');

-- What the functions are: definer rights, an empty search_path, and no execute for anyone but signed-in users.
select pg_temp.assert_true((select count(*) = 2 and bool_and(prosecdef and proconfig @> array['search_path=""'])
  from pg_proc where proname in ('dervo_admin_memberships', 'dervo_admin_membership_audit')), 'definer rights and empty search_path');
create role dervo_testers_nobody nologin;
select pg_temp.assert_true(not has_function_privilege('dervo_testers_nobody', 'public.dervo_admin_memberships()', 'execute'), 'public has no execute on the list');
select pg_temp.assert_true(not has_function_privilege('dervo_testers_nobody', 'public.dervo_admin_membership_audit(bigint, integer)', 'execute'), 'public has no execute on the audit');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_memberships()', 'execute'), 'anon has no execute on the list');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_membership_audit(bigint, integer)', 'execute'), 'anon has no execute on the audit');
select pg_temp.assert_true(has_function_privilege('authenticated', 'public.dervo_admin_memberships()', 'execute'), 'signed-in users may call the list');

select set_config('dervo.tests.members', (select count(*)::text from public.dervo_memberships), false);

set role anon;
select pg_temp.denied('select * from public.dervo_admin_memberships()');
select pg_temp.denied('select * from public.dervo_admin_membership_audit()');
reset role;

set role authenticated;
-- A signed-in tester who isn't an admin, and a missing identity, are refused before anything is read.
select set_config('request.jwt.claim.sub', '10000000-0000-0000-0000-000000000002', false);
select pg_temp.denied('select * from public.dervo_admin_memberships()');
select pg_temp.denied('select * from public.dervo_admin_membership_audit()');
select pg_temp.denied($q$select * from dervo_private.membership_audit$q$);
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied('select * from public.dervo_admin_memberships()');
select pg_temp.denied('select * from public.dervo_admin_membership_audit()');

-- An admin sees everyone with a membership, newest sign-up first, with their email and whether they're an admin.
select set_config('request.jwt.claim.sub', '10000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true((select count(*) from public.dervo_admin_memberships()) = current_setting('dervo.tests.members')::bigint, 'every membership listed');
select pg_temp.assert_true((select email = 'jordan@example.test' from public.dervo_admin_memberships() limit 1), 'newest sign-up first');
select pg_temp.assert_true((select status = 'pending' and not is_admin and signed_up_at is not null and expires_at is null
  from public.dervo_admin_memberships() where email = 'maya@example.test'), 'a new sign-up is pending with its sign-up time');
select pg_temp.assert_true((select is_admin from public.dervo_admin_memberships() where email = 'admin@example.test'), 'admins marked');

-- Changes go through dervo_set_membership, and the audit reads them back newest first, a page at a time.
select public.dervo_set_membership('10000000-0000-0000-0000-000000000002', 'approved', now() + interval '14 days');
select public.dervo_set_membership('10000000-0000-0000-0000-000000000003', 'revoked');
select pg_temp.assert_true((select subject_email = 'jordan@example.test' and new_status = 'revoked' and previous_status = 'pending'
  and actor_email = 'admin@example.test' and actor_id = '10000000-0000-0000-0000-000000000001'::uuid
  from public.dervo_admin_membership_audit() limit 1), 'latest change first, with who did it to whom');
select pg_temp.assert_true((select count(*) = 1 from public.dervo_admin_membership_audit(null, 1)), 'page size honoured');
select pg_temp.assert_true((select count(*) <= 100 from public.dervo_admin_membership_audit(null, 5000)), 'page size capped at 100');
select pg_temp.assert_true((select count(*) >= 1 from public.dervo_admin_membership_audit(null, 0)), 'a page is at least one row');
select pg_temp.assert_true((select bool_and(id < (select max(id) from public.dervo_admin_membership_audit()))
  from public.dervo_admin_membership_audit((select max(id) from public.dervo_admin_membership_audit()), 20)), 'older than before_id only');
select pg_temp.assert_true((select expires_at is not null and new_status = 'approved' from public.dervo_admin_membership_audit()
  where subject_email = 'maya@example.test' limit 1), 'the end date is in the audit');
select pg_temp.assert_true((select status = 'approved' and expires_at > now() from public.dervo_admin_memberships()
  where email = 'maya@example.test'), 'the list shows the change');

-- Reading changes nothing: the functions have no write path, and an admin still can't touch the tables directly.
select pg_temp.denied($q$update public.dervo_memberships set status = 'approved'$q$);
select pg_temp.denied($q$delete from dervo_private.membership_audit$q$);
reset role;
create temporary table testers_audit_before as select count(*) as n from dervo_private.membership_audit;
set role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-0000-0000-000000000001', false);
select count(*) from public.dervo_admin_memberships();
select count(*) from public.dervo_admin_membership_audit();
reset role;
select pg_temp.assert_true((select count(*) from dervo_private.membership_audit) = (select n from testers_audit_before), 'reads leave the audit as it was');

-- Removing the admin record takes effect at once, without changing the session.
delete from dervo_private.admins where user_id = '10000000-0000-0000-0000-000000000001';
set role authenticated;
select set_config('request.jwt.claim.sub', '10000000-0000-0000-0000-000000000001', false);
select pg_temp.denied('select * from public.dervo_admin_memberships()');
select pg_temp.denied('select * from public.dervo_admin_membership_audit()');
reset role;
drop role dervo_testers_nobody;
select 'Dervo testers policy checks passed' as result;
