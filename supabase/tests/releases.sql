-- Disposable PostgreSQL release metadata checks. Installer bytes live in private R2.
create function pg_temp.release_assert(ok boolean, message text) returns void language plpgsql as $$
begin if ok is distinct from true then raise exception '%', message; end if; end;
$$;
create function pg_temp.release_denied(statement text) returns void language plpgsql as $$
begin
  begin execute statement; exception when insufficient_privilege then return; end;
  raise exception 'Expected release permission denial';
end;
$$;
create function pg_temp.release_affected(statement text, expected integer) returns void language plpgsql as $$
declare n integer;
begin execute statement; get diagnostics n = row_count;
  if n <> expected then raise exception 'Unexpected release affected count: %', n; end if;
end;
$$;
insert into auth.users(id) values
 ('00000000-0000-0000-0000-000000000011'),
 ('00000000-0000-0000-0000-000000000012');
update public.dervo_memberships set status='approved'
  where user_id='00000000-0000-0000-0000-000000000011';
insert into public.dervo_beta_releases
 (release_id, build_number, product_version, title, notes, format, object_name, sha256, bytes, published_at, withdrawn, download_origin)
values
 ('trial.9',9,'0.1.0','Nine','Published ZIP','zip','trial.9/Dervo-macos-arm64.zip',repeat('a',64),100,now()-interval '1 hour',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com'),
 ('trial.10',10,'0.1.0','Ten','Published DMG','dmg','trial.10/Dervo-macos-arm64.dmg',repeat('b',64),101,now()-interval '1 minute',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com'),
 ('draft',11,'0.1.0','Draft','Not approved','zip','draft/Dervo-macos-arm64.zip',repeat('c',64),102,null,false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com'),
 ('withdrawn',12,'0.1.0','Withdrawn','Removed','zip','withdrawn/Dervo-macos-arm64.zip',repeat('d',64),103,now()-interval '1 minute',true,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com'),
 ('future',13,'0.1.0','Future','Not released yet','zip','future/Dervo-macos-arm64.zip',repeat('e',64),104,now()+interval '1 day',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');

set role anon;
select set_config('request.jwt.claim.sub','',false);
select pg_temp.release_denied('select * from public.dervo_beta_releases');
reset role;
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000012',false);
select pg_temp.release_assert((select count(*)=0 from public.dervo_beta_releases),'pending releases hidden');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000011',false);
select pg_temp.release_assert((select count(*)=2 from public.dervo_beta_releases),'only published releases visible');
select pg_temp.release_assert((select release_id='trial.10' from public.dervo_beta_releases order by build_number desc limit 1),'numeric newest release');
select pg_temp.release_denied($q$update public.dervo_beta_releases set published_at=now()$q$);
select pg_temp.release_denied($q$delete from public.dervo_beta_releases$q$);
select pg_temp.release_denied($q$insert into public.dervo_beta_releases select * from public.dervo_beta_releases$q$);
reset role;
update public.dervo_memberships set expires_at=now()-interval '1 second'
  where user_id='00000000-0000-0000-0000-000000000011';
set role authenticated;
select pg_temp.release_assert((select count(*)=0 from public.dervo_beta_releases),'expired metadata denied');
reset role;
update public.dervo_memberships set status='revoked',expires_at=null
  where user_id='00000000-0000-0000-0000-000000000011';
set role authenticated;
select pg_temp.release_assert((select count(*)=0 from public.dervo_beta_releases),'revoked metadata denied');
reset role;
select pg_temp.release_assert((select count(*)=0 from storage.buckets where id='dervo-beta-releases'),'no Supabase installer bucket created');
-- Release signatures (202609190003): 128 lowercase hex characters or nothing. Testers still can't write one.
update public.dervo_beta_releases set signature=repeat('0f',64) where release_id='trial.10';
select pg_temp.release_assert((select signature is null from public.dervo_beta_releases where release_id='trial.9'),'unsigned rows stay readable');
do $$ begin
  update public.dervo_beta_releases set signature=repeat('0F',64) where release_id='trial.9';
  raise exception 'Expected an uppercase signature to be refused';
exception when check_violation then null; end $$;
do $$ begin
  update public.dervo_beta_releases set signature='signed by Bryce' where release_id='trial.9';
  raise exception 'Expected a text signature to be refused';
exception when check_violation then null; end $$;
update public.dervo_memberships set status='approved' where user_id='00000000-0000-0000-0000-000000000011';
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000011',false);
select pg_temp.release_assert((select signature=repeat('0f',64) from public.dervo_beta_releases where release_id='trial.10'),'members read the signature');
select pg_temp.release_denied($q$update public.dervo_beta_releases set signature=repeat('ab',64)$q$);
reset role;
-- Admin previews (202609240001): a preview row is read by approved, unexpired admins only. A tester, an admin whose
-- membership lapsed, and anyone signed out see none of it; testers' newest release stays the newest beta.
insert into auth.users(id) values ('00000000-0000-0000-0000-000000000013');
update public.dervo_memberships set status='approved' where user_id='00000000-0000-0000-0000-000000000013';
insert into dervo_private.admins(user_id) values ('00000000-0000-0000-0000-000000000013');
insert into public.dervo_beta_releases
 (release_id, build_number, product_version, channel, title, notes, format, object_name, sha256, bytes, published_at, withdrawn, download_origin)
values
 ('preview.20',20,'0.1.0','admin-preview','Preview','Admins only','zip','preview.20/Dervo-macos-arm64.zip',repeat('f',64),105,now()-interval '1 minute',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');
do $$ begin
  insert into public.dervo_beta_releases
   (release_id, build_number, product_version, channel, title, notes, format, object_name, sha256, bytes, download_origin)
  values ('odd',21,'0.1.0','nightly','Odd','No such channel','zip','odd/Dervo-macos-arm64.zip',repeat('1',64),1,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');
  raise exception 'Expected an unknown channel to be refused';
exception when check_violation then null; end $$;
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000011',false);
select pg_temp.release_assert((select count(*)=0 from public.dervo_beta_releases where channel='admin-preview'),'testers never see admin previews');
select pg_temp.release_assert((select release_id='trial.10' from public.dervo_beta_releases order by build_number desc limit 1),'a tester''s newest is still the newest beta');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000013',false);
select pg_temp.release_assert((select release_id='preview.20' from public.dervo_beta_releases order by build_number desc limit 1),'an admin''s newest is the preview');
select pg_temp.release_assert((select count(*)=3 from public.dervo_beta_releases),'an admin also reads the betas');
select pg_temp.release_assert((select public.dervo_is_admin()),'the admin check answers for the caller');
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000011',false);
select pg_temp.release_assert((select not public.dervo_is_admin()),'and says no for a tester');
reset role;
update public.dervo_memberships set expires_at=now()-interval '1 second' where user_id='00000000-0000-0000-0000-000000000013';
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000013',false);
select pg_temp.release_assert((select count(*)=0 from public.dervo_beta_releases),'an admin whose membership lapsed reads nothing');
reset role;
set role anon;
select set_config('request.jwt.claim.sub','',false);
select pg_temp.release_denied('select * from public.dervo_beta_releases');
reset role;
-- Windows releases (202609260001): a Windows row has its own -windows release id, an .exe and its own object name.
-- Each computer asks for its own platform, so a tester's newest Mac release is unchanged by a Windows row.
insert into public.dervo_beta_releases
 (release_id, build_number, product_version, platform, title, notes, format, object_name, sha256, bytes, published_at, withdrawn, download_origin)
values
 ('trial.30-windows',30,'0.1.0','windows-x64','Windows','First Windows build','exe','trial.30-windows/Dervo-windows-x64.exe',repeat('2',64),106,now()-interval '1 minute',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');
create function pg_temp.release_refused(platform text, release_id text, format text, object_name text) returns void
language plpgsql as $$
begin
  begin
    insert into public.dervo_beta_releases
     (release_id, build_number, product_version, platform, title, notes, format, object_name, sha256, bytes, download_origin)
    values (release_id,31,'0.1.0',platform,'Refused','Refused',format,object_name,repeat('3',64),1,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');
  exception when check_violation then return; end;
  raise exception 'Expected % % % % to be refused', platform, release_id, format, object_name;
end;
$$;
select pg_temp.release_refused('windows-x64','w.1-windows','zip','w.1-windows/Dervo-windows-x64.zip');
select pg_temp.release_refused('windows-x64','w.1-windows','exe','w.1-windows/Dervo-macos-arm64.exe');
select pg_temp.release_refused('windows-x64','w.1','exe','w.1/Dervo-windows-x64.exe');
select pg_temp.release_refused('macos-arm64','m.1-windows','zip','m.1-windows/Dervo-macos-arm64.zip');
select pg_temp.release_refused('macos-arm64','m.1','exe','m.1/Dervo-macos-arm64.exe');
select pg_temp.release_refused('linux-x64','l.1','zip','l.1/Dervo-linux-x64.zip');
-- Linux releases (202610080001): one row per package format, its id ending in -linux-<format>, with a build of its own.
insert into public.dervo_beta_releases
 (release_id, build_number, product_version, platform, title, notes, format, object_name, sha256, bytes, published_at, withdrawn, download_origin)
values
 ('trial.40-linux-deb',40,'0.1.0','linux-x64','Linux','First Linux build','deb','trial.40-linux-deb/Dervo-linux-x64.deb',repeat('4',64),107,now()-interval '1 minute',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com'),
 ('trial.41-linux-appimage',41,'0.1.0','linux-x64','Linux','First Linux build','appimage','trial.41-linux-appimage/Dervo-linux-x64.appimage',repeat('5',64),108,now()-interval '1 minute',false,'https://' || repeat('a',32) || '.r2.cloudflarestorage.com');
select pg_temp.release_refused('linux-x64','l.2-linux-rpm','deb','l.2-linux-rpm/Dervo-linux-x64.deb');
select pg_temp.release_refused('linux-x64','l.2-linux','deb','l.2-linux/Dervo-linux-x64.deb');
select pg_temp.release_refused('linux-x64','l.2-linux-deb','exe','l.2-linux-deb/Dervo-linux-x64.exe');
select pg_temp.release_refused('macos-arm64','m.2-linux-deb','zip','m.2-linux-deb/Dervo-macos-arm64.zip');
select pg_temp.release_refused('windows-x64','w.2-linux-deb-windows','deb','w.2-linux-deb-windows/Dervo-windows-x64.deb');
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000011',false);
select pg_temp.release_assert((select release_id='trial.30-windows' from public.dervo_beta_releases where platform='windows-x64' order by build_number desc limit 1),'a tester on Windows reads the Windows row');
select pg_temp.release_assert((select release_id='trial.10' from public.dervo_beta_releases where platform='macos-arm64' order by build_number desc limit 1),'a tester on a Mac still gets the newest Mac beta');
select pg_temp.release_assert((select release_id='trial.40-linux-deb' from public.dervo_beta_releases where platform='linux-x64' and format='deb' order by build_number desc limit 1),'a .deb install reads the newest .deb');
select pg_temp.release_assert((select release_id='trial.41-linux-appimage' from public.dervo_beta_releases where platform='linux-x64' and format='appimage' order by build_number desc limit 1),'an AppImage reads the newest AppImage');
reset role;
