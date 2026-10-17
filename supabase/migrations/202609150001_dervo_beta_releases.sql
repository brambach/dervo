-- Prepare private update discovery. No release or tester is approved by this migration.
begin;

create table public.dervo_beta_releases (
  release_id text primary key check (release_id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$'),
  build_number numeric(20,0) not null unique check (build_number > 0),
  product_version text not null check (product_version ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'),
  channel text not null default 'private-beta' check (channel = 'private-beta'),
  platform text not null default 'macos-arm64' check (platform = 'macos-arm64'),
  title text not null check (char_length(title) between 1 and 120),
  notes text not null check (char_length(notes) between 1 and 4000),
  format text not null check (format in ('zip', 'dmg')),
  object_name text not null unique,
  download_origin text not null check (download_origin ~ '^https://[a-f0-9]{32}\.r2\.cloudflarestorage\.com$'),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  bytes bigint not null check (bytes between 1 and 2147483648),
  published_at timestamptz,
  withdrawn boolean not null default false,
  check (object_name = release_id || '/Dervo-macos-arm64.' || format)
);
alter table public.dervo_beta_releases enable row level security;
revoke all on public.dervo_beta_releases from public, anon, authenticated;
grant select on public.dervo_beta_releases to authenticated;

-- Authenticated callers can read only approved, unexpired beta releases.
-- A server admin must publish an exact artifact separately after owner approval.
create policy dervo_release_read on public.dervo_beta_releases for select to authenticated
  using (published_at <= now() and not withdrawn and exists (
    select 1 from public.dervo_memberships m
    where m.user_id = (select auth.uid()) and m.status = 'approved'
      and m.release_channel = channel and (m.expires_at is null or m.expires_at > now())
  ));

commit;
