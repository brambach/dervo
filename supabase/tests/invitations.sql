-- Beta invitations (migrations/202609220001_dervo_invitations.sql), on an isolated PostgreSQL cluster with
-- Supabase-shaped test schemas. Checks who may invite, idempotency and what a verified sign-in gets. Not hosted Auth,
-- PostgREST, the website Worker or any email.
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
create function pg_temp.invalid(statement text) returns void language plpgsql as $$
begin
  begin execute statement;
  exception when invalid_parameter_value then return;
  end;
  raise exception 'Expected an invalid value: %', statement;
end;
$$;

insert into auth.users(id, email) values
 ('20000000-0000-0000-0000-000000000001', 'invite-admin@example.test'),
 ('20000000-0000-0000-0000-000000000002', 'invite-tester@example.test'),
 ('20000000-0000-0000-0000-000000000003', 'Pending.Person@example.test'),
 ('20000000-0000-0000-0000-000000000004', 'revoked-person@example.test');
insert into dervo_private.admins(user_id) values ('20000000-0000-0000-0000-000000000001');
update public.dervo_memberships set status = 'approved' where user_id = '20000000-0000-0000-0000-000000000002';
update public.dervo_memberships set status = 'revoked' where user_id = '20000000-0000-0000-0000-000000000004';

select pg_temp.assert_true((select count(*) = 3 and bool_and(prosecdef and proconfig @> array['search_path=""'])
  from pg_proc where proname in ('dervo_admin_invite', 'dervo_admin_invitations', 'dervo_admin_revoke_invitation')),
  'definer rights and empty search_path');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_invite(text, boolean)', 'execute'), 'anon has no execute on invite');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_invitations()', 'execute'), 'anon has no execute on the list');
select pg_temp.assert_true(not has_function_privilege('anon', 'public.dervo_admin_revoke_invitation(text)', 'execute'), 'anon has no execute on revoke');
select pg_temp.assert_true(not has_function_privilege('authenticated', 'dervo_private.accept_invitation()', 'execute'), 'nobody calls the trigger function');

set role authenticated;
-- A tester who isn't an admin, and a missing identity, are refused before anything is read or written.
select set_config('request.jwt.claim.sub', '20000000-0000-0000-0000-000000000002', false);
select pg_temp.denied($q$select public.dervo_admin_invite('new@example.test')$q$);
select pg_temp.denied('select * from public.dervo_admin_invitations()');
select pg_temp.denied($q$select public.dervo_admin_revoke_invitation('new@example.test')$q$);
select pg_temp.denied('select * from dervo_private.invitations');
select pg_temp.denied('select * from dervo_private.invitation_audit');
select set_config('request.jwt.claim.sub', '', false);
select pg_temp.denied($q$select public.dervo_admin_invite('new@example.test')$q$);

-- An admin invites. The email is normalised, asking again returns the same ID, and only a resend rotates it.
select set_config('request.jwt.claim.sub', '20000000-0000-0000-0000-000000000001', false);
select pg_temp.invalid($q$select public.dervo_admin_invite('not an email')$q$);
select pg_temp.invalid($q$select public.dervo_admin_invite(null)$q$);
select set_config('dervo.tests.first', public.dervo_admin_invite('  New@Example.test ')::text, false);
select pg_temp.assert_true(current_setting('dervo.tests.first')::jsonb->>'state' = 'created', 'first invite is created');
select pg_temp.assert_true((current_setting('dervo.tests.first')::jsonb->>'expires_at')::timestamptz
  between now() + interval '13 days' and now() + interval '15 days', 'two weeks to accept');
select set_config('dervo.tests.second', public.dervo_admin_invite('new@example.test')::text, false);
select pg_temp.assert_true(current_setting('dervo.tests.second')::jsonb->>'state' = 'existing', 'a repeat is the same invitation');
select pg_temp.assert_true(current_setting('dervo.tests.second')::jsonb->>'invite_id' = current_setting('dervo.tests.first')::jsonb->>'invite_id', 'same ID on a repeat');
select set_config('dervo.tests.third', public.dervo_admin_invite('new@example.test', true)::text, false);
select pg_temp.assert_true(current_setting('dervo.tests.third')::jsonb->>'state' = 'renewed', 'resend renews');
select pg_temp.assert_true(current_setting('dervo.tests.third')::jsonb->>'invite_id' <> current_setting('dervo.tests.first')::jsonb->>'invite_id', 'resend rotates the ID');
select pg_temp.assert_true((select count(*) = 1 and min(i.email) = 'new@example.test' and min(i.status) = 'invited'
  from public.dervo_admin_invitations() i), 'one row per email');

-- Members: approved and revoked are reported and left alone; a confirmed pending account is approved at once, audited.
select pg_temp.assert_true(public.dervo_admin_invite('invite-tester@example.test')->>'state' = 'already_approved', 'approved member reported');
select pg_temp.assert_true(public.dervo_admin_invite('revoked-person@example.test')->>'state' = 'member_revoked', 'revoked member reported');
select set_config('dervo.tests.pending', public.dervo_admin_invite('pending.person@example.test')::text, false);
select pg_temp.assert_true(current_setting('dervo.tests.pending')::jsonb->>'state' = 'created', 'pending member invited');
select pg_temp.assert_true(public.dervo_admin_invite('pending.person@example.test')->>'state' = 'already_approved', 'and approved by it');
select pg_temp.assert_true(public.dervo_admin_invite('pending.person@example.test')->>'invite_id' = current_setting('dervo.tests.pending')::jsonb->>'invite_id', 'email retry can identify the same accepted invitation');
reset role;
select pg_temp.assert_true((select status = 'revoked' from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000004'), 'an invitation never undoes a revocation');
select pg_temp.assert_true((select count(*) = 1 from dervo_private.membership_audit
  where actor_id = '20000000-0000-0000-0000-000000000001' and subject_id = '20000000-0000-0000-0000-000000000003'
    and previous_status = 'pending' and new_status = 'approved'), 'pending member approval audited to the admin');

-- A new person signs up with the invited email. Unverified, they stay pending; verified, they're approved and audited.
insert into auth.users(id, email, email_confirmed_at) values ('20000000-0000-0000-0000-000000000005', 'NEW@example.test', null);
select pg_temp.assert_true((select status = 'pending' from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000005'), 'unverified stays pending');
update auth.users set email_confirmed_at = now() where id = '20000000-0000-0000-0000-000000000005';
select pg_temp.assert_true((select status = 'approved' and expires_at is null from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000005'), 'verified sign-in is approved');
select pg_temp.assert_true((select count(*) = 1 from dervo_private.membership_audit
  where actor_id = '20000000-0000-0000-0000-000000000001' and subject_id = '20000000-0000-0000-0000-000000000005'
    and previous_status = 'pending' and new_status = 'approved'), 'approval audited to the inviting admin');
select pg_temp.assert_true((select status = 'accepted' and accepted_user = '20000000-0000-0000-0000-000000000005'
  from dervo_private.invitations where email = 'new@example.test'), 'invitation marked accepted');
select pg_temp.assert_true((select array_agg(action order by id) = array['invited', 'renewed', 'accepted']
  from dervo_private.invitation_audit where email = 'new@example.test'), 'invitation audit trail');

-- Someone who wasn't invited, an expired invitation and a revoked invitation all stay pending.
insert into auth.users(id, email) values ('20000000-0000-0000-0000-000000000006', 'stranger@example.test');
select pg_temp.assert_true((select status = 'pending' from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000006'), 'uninvited stays pending');
insert into auth.users(id, email, raw_user_meta_data) values ('20000000-0000-0000-0000-000000000009', 'forwarded@example.test',
  jsonb_build_object('email', 'new@example.test', 'invite_id', current_setting('dervo.tests.third')::jsonb->>'invite_id'));
select pg_temp.assert_true((select status = 'pending' from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000009'), 'forwarded link or invitation metadata never approves a different email');
set role authenticated;
select pg_temp.assert_true(public.dervo_admin_invite('late@example.test')->>'state' = 'created', 'late invited');
select pg_temp.assert_true(public.dervo_admin_invite('withdrawn@example.test')->>'state' = 'created', 'withdrawn invited');
select pg_temp.assert_true(public.dervo_admin_revoke_invitation('Withdrawn@example.test ')->>'state' = 'revoked', 'invitation revoked');
select pg_temp.assert_true(public.dervo_admin_revoke_invitation('withdrawn@example.test')->>'state' = 'revoked', 'revoking twice is the same');
select pg_temp.assert_true(public.dervo_admin_revoke_invitation('new@example.test')->>'state' = 'accepted', 'an accepted invitation is a membership');
select pg_temp.assert_true(public.dervo_admin_revoke_invitation('nobody@example.test')->>'state' = 'not_found', 'unknown email');
reset role;
update dervo_private.invitations set expires_at = now() - interval '1 minute' where email = 'late@example.test';
insert into auth.users(id, email) values
 ('20000000-0000-0000-0000-000000000007', 'late@example.test'),
 ('20000000-0000-0000-0000-000000000008', 'withdrawn@example.test');
select pg_temp.assert_true((select bool_and(status = 'pending') from public.dervo_memberships
  where user_id in ('20000000-0000-0000-0000-000000000007', '20000000-0000-0000-0000-000000000008')), 'expired and revoked invitations approve nobody');
select pg_temp.assert_true((select count(*) = 1 from dervo_private.invitation_audit where email = 'withdrawn@example.test' and action = 'revoked'), 'revocation audited once');

-- An expired invitation is renewed by a plain invite, and the waiting account is approved by it.
set role authenticated;
select pg_temp.assert_true(public.dervo_admin_invite('late@example.test')->>'state' = 'renewed', 'expired invitation renewed');
reset role;
select pg_temp.assert_true((select status = 'approved' from public.dervo_memberships where user_id = '20000000-0000-0000-0000-000000000007'), 'renewal approves the waiting account');
select set_config('request.jwt.claim.sub', '', false);
