-- Linux releases (Bryce, 8 Oct 2026: Linux and Windows builds shipped with every release). Each Linux package format is
-- its own row, the way Windows got its own rows in 202609260001: an id ending in -linux-deb, -linux-rpm or
-- -linux-appimage, a build number of its own (scripts/linux-release.py stamps each format separately) and the format
-- the id names. An installed Dervo asks only for its own platform and format (backend/beta_updates.py), so a .deb install
-- is offered the next .deb. Mac and Windows rows read exactly as before. No release, tester or admin is added or
-- changed, and reading stays as before. Applying it is a hosted change: Bryce's go first.
begin;

alter table public.dervo_beta_releases drop constraint dervo_beta_releases_platform_check;
alter table public.dervo_beta_releases drop constraint dervo_beta_releases_format_check;

alter table public.dervo_beta_releases
  add constraint dervo_beta_releases_platform_check check (platform in ('macos-arm64', 'windows-x64', 'linux-x64')),
  add constraint dervo_beta_releases_format_check check (
    (platform = 'macos-arm64' and format in ('zip', 'dmg')) or (platform = 'windows-x64' and format = 'exe')
    or (platform = 'linux-x64' and format in ('deb', 'rpm', 'appimage'))),
  add constraint dervo_beta_releases_release_id_linux_check check (
    (platform = 'linux-x64') = (release_id like '%-linux-' || format)
    and (platform = 'linux-x64' or release_id !~ '-linux-(deb|rpm|appimage)$'));

commit;
