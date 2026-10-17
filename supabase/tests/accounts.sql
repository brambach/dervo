-- Executed against an isolated PostgreSQL cluster with Supabase-shaped test schemas.
-- This checks SQL permissions and policies, not hosted Auth or Storage delivery.
create function pg_temp.assert_true(value boolean, label text) returns void language plpgsql as $$
begin if value is distinct from true then raise exception 'Assertion failed: %', label; end if; end;
$$;
create function pg_temp.denied(statement text) returns void language plpgsql as $$
begin
  begin execute statement;
  exception when insufficient_privilege then return;
  end;
  raise exception 'Expected permission denial';
end;
$$;
create function pg_temp.affected(statement text, expected integer) returns void language plpgsql as $$
declare count_rows integer;
begin execute statement; get diagnostics count_rows = row_count;
  if count_rows <> expected then raise exception 'Unexpected affected row count: %', count_rows; end if;
end;
$$;

insert into auth.users(id, raw_user_meta_data) values
 ('00000000-0000-0000-0000-000000000001', '{"role":"admin","status":"approved"}'),
 ('00000000-0000-0000-0000-000000000002', '{}'),
 ('00000000-0000-0000-0000-000000000003', '{}');
insert into dervo_private.admins(user_id) values ('00000000-0000-0000-0000-000000000003');
select pg_temp.assert_true((select count(*)=4 from public.dervo_profiles), 'new users plus migration backfill');

set role anon;
select pg_temp.denied('select * from public.dervo_profiles');
select pg_temp.denied('select * from public.dervo_memberships');
select pg_temp.denied('select public.dervo_access()');
select pg_temp.denied($q$select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved')$q$);
reset role;

set role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', false);
select pg_temp.assert_true((select count(*)=1 from public.dervo_profiles), 'profiles restricted to owner');
select pg_temp.assert_true((select count(*)=1 from public.dervo_memberships), 'membership restricted to owner');
select pg_temp.assert_true(public.dervo_access()->>'status'='pending', 'signup metadata cannot approve access');
select pg_temp.assert_true(public.dervo_access()->>'allowed'='false', 'pending denied');
select pg_temp.assert_true(public.dervo_access()->>'is_admin'='false', 'signup metadata cannot grant admin');
select pg_temp.affected($q$update public.dervo_profiles set display_name='Fieldnotes tester' where user_id='00000000-0000-0000-0000-000000000001'$q$,1);
select pg_temp.affected($q$update public.dervo_profiles set display_name='Other user' where user_id='00000000-0000-0000-0000-000000000002'$q$,0);
select pg_temp.denied($q$update public.dervo_profiles set user_id='00000000-0000-0000-0000-000000000002'$q$);
select pg_temp.denied($q$update public.dervo_profiles set created_at=now()$q$);
select pg_temp.denied($q$delete from public.dervo_profiles$q$);
select pg_temp.denied($q$insert into public.dervo_profiles(user_id) values ('00000000-0000-0000-0000-000000000004')$q$);
select pg_temp.denied($q$update public.dervo_memberships set status='approved'$q$);
select pg_temp.denied($q$delete from public.dervo_memberships$q$);
select pg_temp.denied($q$select * from dervo_private.admins$q$);
select pg_temp.denied($q$insert into dervo_private.admins(user_id) values ('00000000-0000-0000-0000-000000000001')$q$);
select pg_temp.denied($q$select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved')$q$);
select pg_temp.denied($q$select * from dervo_private.membership_audit$q$);

insert into storage.objects(bucket_id,name) values ('dervo-avatars','00000000-0000-0000-0000-000000000001/photo.png');
select pg_temp.denied($q$insert into storage.objects(bucket_id,name) values ('dervo-avatars','00000000-0000-0000-0000-000000000002/photo.png')$q$);
select pg_temp.denied($q$update storage.objects set name='00000000-0000-0000-0000-000000000002/photo.png'$q$);
select pg_temp.denied($q$insert into storage.objects(bucket_id,name) values ('other','00000000-0000-0000-0000-000000000001/photo.png')$q$);
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000002',false);
select pg_temp.assert_true((select count(*)=0 from storage.objects),'cross-user avatar read denied');
select pg_temp.affected($q$delete from storage.objects$q$,0);
select pg_temp.affected($q$update storage.objects set name='00000000-0000-0000-0000-000000000002/renamed.png'$q$,0);

select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000003',false);
select pg_temp.assert_true(public.dervo_access()->>'is_admin'='true','server admin recognised');
select pg_temp.assert_true(public.dervo_access()->>'allowed'='false','admin role alone does not grant beta access');
select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved',now()+interval '1 day');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',false);
select pg_temp.assert_true(public.dervo_access()->>'allowed'='true','approved access allowed');
select pg_temp.assert_true(public.dervo_access()->>'is_admin'='false','tester approval does not grant admin');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000003',false);
select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved',now()-interval '1 second');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',false);
select pg_temp.assert_true(public.dervo_access()->>'allowed'='false','expired access denied');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000003',false);
select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','revoked');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',false);
select pg_temp.assert_true(public.dervo_access()->>'allowed'='false','revoked access denied');
select pg_temp.assert_true(public.dervo_access()->>'status'='revoked','revocation visible');
select pg_temp.affected($q$delete from storage.objects$q$,1);
reset role;
select pg_temp.assert_true((select count(*)=3 from dervo_private.membership_audit),'membership changes audited');
select pg_temp.assert_true((select not public and file_size_limit=2097152 and cardinality(allowed_mime_types)=3 from storage.buckets where id='dervo-avatars'),'private avatar limits configured');

-- A policy from another Storage feature mustn't weaken this bucket's ownership.
insert into storage.buckets(id,name,public) values ('other','other',false);
create policy unrelated_storage_feature on storage.objects for all to public using (true) with check (true);
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000001',false);
insert into storage.objects(bucket_id,name) values ('dervo-avatars','00000000-0000-0000-0000-000000000001/positive.png');
select pg_temp.assert_true((select count(*)=1 from storage.objects where bucket_id='dervo-avatars'),'own avatar read with broad policy');
select pg_temp.affected($q$update storage.objects set name='00000000-0000-0000-0000-000000000001/renamed.png' where bucket_id='dervo-avatars'$q$,1);
select pg_temp.denied($q$insert into storage.objects(bucket_id,name) values ('dervo-avatars','00000000-0000-0000-0000-000000000002/other.png')$q$);
select pg_temp.denied($q$update storage.objects set name='00000000-0000-0000-0000-000000000002/stolen.png' where bucket_id='dervo-avatars'$q$);
insert into storage.objects(bucket_id,name) values ('other','unrelated-feature.png');
select pg_temp.affected($q$update storage.objects set name='renamed-feature.png' where bucket_id='other'$q$,1);
select pg_temp.denied($q$update storage.objects set bucket_id='dervo-avatars',name='00000000-0000-0000-0000-000000000002/moved.png' where bucket_id='other'$q$);
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000002',false);
select pg_temp.assert_true((select count(*)=0 from storage.objects where bucket_id='dervo-avatars'),'broad read policy cannot reveal another avatar');
select pg_temp.affected($q$update storage.objects set name='00000000-0000-0000-0000-000000000002/stolen.png' where bucket_id='dervo-avatars'$q$,0);
select pg_temp.affected($q$delete from storage.objects where bucket_id='dervo-avatars'$q$,0);
select set_config('request.jwt.claim.sub','',false);
select pg_temp.assert_true(public.dervo_access()->>'allowed'='false','missing identity has no beta access');
select pg_temp.assert_true(public.dervo_access()->>'is_admin'='false','missing identity has no admin');
select pg_temp.denied($q$select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved')$q$);
set role anon;
select pg_temp.assert_true((select count(*)=0 from storage.objects where bucket_id='dervo-avatars'),'broad policy cannot reveal an avatar anonymously');
select pg_temp.denied($q$insert into storage.objects(bucket_id,name) values ('dervo-avatars','00000000-0000-0000-0000-000000000001/anonymous.png')$q$);
select pg_temp.affected($q$update storage.objects set name='anonymous.png' where bucket_id='dervo-avatars'$q$,0);
select pg_temp.affected($q$delete from storage.objects where bucket_id='dervo-avatars'$q$,0);
select pg_temp.assert_true((select count(*)=1 from storage.objects where bucket_id='other'),'guards preserve other bucket policies');
reset role;

-- Removing the trusted admin record takes effect without changing the session.
select pg_temp.assert_true((select bool_and(actor_id='00000000-0000-0000-0000-000000000003'::uuid and subject_id='00000000-0000-0000-0000-000000000001'::uuid) from dervo_private.membership_audit),'audit actor and subject use the verified caller');
delete from dervo_private.admins where user_id='00000000-0000-0000-0000-000000000003';
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000003',false);
select pg_temp.assert_true(public.dervo_access()->>'is_admin'='false','admin revocation is immediate');
select pg_temp.denied($q$select public.dervo_set_membership('00000000-0000-0000-0000-000000000001','approved')$q$);
select pg_temp.denied($q$insert into public.dervo_memberships(user_id,status) values ('00000000-0000-0000-0000-000000000004','approved')$q$);
select pg_temp.denied($q$update public.dervo_memberships set expires_at=null$q$);
select pg_temp.denied($q$insert into dervo_private.membership_audit(actor_id,subject_id,new_status) values ('00000000-0000-0000-0000-000000000003','00000000-0000-0000-0000-000000000001','approved')$q$);
reset role;
select pg_temp.assert_true((select count(*)=3 from dervo_private.membership_audit),'denied changes leave audit unchanged');
select 'Dervo account policy checks passed' as result;
