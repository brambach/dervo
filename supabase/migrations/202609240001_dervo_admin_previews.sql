-- Admin previews (Bryce, 24 Sep 2026): a preview build is offered in Settings > Updates to admins only, so Bryce
-- updates his own Dervo from inside the app while testers see only the numbered betas published on the private-beta
-- channel. The database decides who sees a row; the app asks for both channels and gets what it may read.
-- No release, tester or admin is added or changed by this migration.
begin;

alter table public.dervo_beta_releases drop constraint dervo_beta_releases_channel_check;
alter table public.dervo_beta_releases
  add constraint dervo_beta_releases_channel_check check (channel in ('private-beta', 'admin-preview'));

-- Whether the caller is a Dervo admin. Policies run as the caller, who can't read dervo_private, so this reads it
-- as its owner with an empty search_path and every name qualified, like dervo_access().
create function public.dervo_is_admin() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from dervo_private.admins a where a.user_id = (select auth.uid()));
$$;
revoke all on function public.dervo_is_admin() from public, anon, authenticated;
grant execute on function public.dervo_is_admin() to authenticated;

-- Approved, unexpired members read published rows on their own channel, as before. An approved, unexpired member who
-- is also an admin reads the admin-preview channel too. Drafts, future and withdrawn rows stay hidden from everyone.
drop policy dervo_release_read on public.dervo_beta_releases;
create policy dervo_release_read on public.dervo_beta_releases for select to authenticated
  using (published_at <= now() and not withdrawn and exists (
    select 1 from public.dervo_memberships m
    where m.user_id = (select auth.uid()) and m.status = 'approved'
      and (m.expires_at is null or m.expires_at > now())
      and (m.release_channel = channel or (channel = 'admin-preview' and (select public.dervo_is_admin())))
  ));

commit;
