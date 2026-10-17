-- Windows releases (b16/windows, milestone 5 in PLAN-windows-m5-20260926.md). A Windows installer gets its own row with
-- its own release id, which ends in -windows (Bryce, 26 Sep 2026: "own id per platform"), and its own build number, so
-- release_id stays the key, build numbers stay unique, and the release signature over release id, build, version and
-- SHA-256 needs no change. A Mac row reads exactly as before: macos-arm64, a ZIP or DMG, no -windows in its id.
-- The app asks only for its own platform (backend/beta_updates.py), so a Mac never sees a Windows row or the other way
-- round. No release, tester or admin is added or changed by this migration, and reading stays as before.
begin;

-- The object name check was declared without a name in 202609150001. Find it by what it says rather than guess its
-- generated name, and stop if there isn't exactly one, so nothing Mac-only is left behind unnoticed.
do $$
declare
  found text[];
begin
  select array_agg(conname) into found from pg_constraint
    where conrelid = 'public.dervo_beta_releases'::regclass and contype = 'c'
      and pg_get_constraintdef(oid) like '%Dervo-macos-arm64%';
  if coalesce(array_length(found, 1), 0) <> 1 then
    raise exception 'Expected one Mac-only object name check on dervo_beta_releases, found %', coalesce(found, '{}');
  end if;
  execute format('alter table public.dervo_beta_releases drop constraint %I', found[1]);
end $$;

alter table public.dervo_beta_releases drop constraint dervo_beta_releases_platform_check;
alter table public.dervo_beta_releases drop constraint dervo_beta_releases_format_check;

alter table public.dervo_beta_releases
  add constraint dervo_beta_releases_platform_check check (platform in ('macos-arm64', 'windows-x64')),
  add constraint dervo_beta_releases_format_check check (
    (platform = 'macos-arm64' and format in ('zip', 'dmg')) or (platform = 'windows-x64' and format = 'exe')),
  add constraint dervo_beta_releases_object_name_check check (
    object_name = release_id || '/Dervo-' || platform || '.' || format),
  add constraint dervo_beta_releases_release_id_platform_check check (
    (platform = 'windows-x64') = (release_id like '%-windows'));

commit;
