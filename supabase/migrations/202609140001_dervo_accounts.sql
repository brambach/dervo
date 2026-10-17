-- Dervo identity and private-beta access. Apply only to the selected Dervo project.
begin;

create schema if not exists dervo_private;
revoke all on schema dervo_private from public, anon, authenticated;

create table public.dervo_profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  display_name text not null default '' check (char_length(display_name) <= 80),
  created_at timestamptz not null default now()
);
create table public.dervo_memberships (
  user_id uuid primary key references auth.users(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'approved', 'revoked')),
  release_channel text not null default 'private-beta' check (release_channel = 'private-beta'),
  expires_at timestamptz,
  updated_at timestamptz not null default now()
);
create table dervo_private.admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
create table dervo_private.membership_audit (
  id bigint generated always as identity primary key,
  actor_id uuid not null,
  subject_id uuid not null,
  previous_status text,
  new_status text not null,
  expires_at timestamptz,
  changed_at timestamptz not null default now()
);

alter table public.dervo_profiles enable row level security;
alter table public.dervo_memberships enable row level security;
alter table dervo_private.admins enable row level security;
alter table dervo_private.membership_audit enable row level security;
revoke all on public.dervo_profiles, public.dervo_memberships from public, anon, authenticated;
revoke all on dervo_private.admins, dervo_private.membership_audit from public, anon, authenticated;
grant select on public.dervo_profiles, public.dervo_memberships to authenticated;
grant update (display_name) on public.dervo_profiles to authenticated;
create policy dervo_profile_read on public.dervo_profiles for select to authenticated
  using ((select auth.uid()) = user_id);
create policy dervo_profile_edit on public.dervo_profiles for update to authenticated
  using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy dervo_membership_read on public.dervo_memberships for select to authenticated
  using ((select auth.uid()) = user_id);

-- Metadata supplied during signup never selects roles or membership status.
create function dervo_private.new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  insert into public.dervo_profiles(user_id) values (new.id);
  insert into public.dervo_memberships(user_id) values (new.id);
  return new;
end;
$$;
revoke all on function dervo_private.new_user() from public, anon, authenticated;
create trigger dervo_new_user after insert on auth.users
  for each row execute function dervo_private.new_user();
-- Existing identities also begin pending. This doesn't grant tester or admin access.
insert into public.dervo_profiles(user_id) select id from auth.users;
insert into public.dervo_memberships(user_id) select id from auth.users;

create function public.dervo_access() returns jsonb
language sql stable security definer set search_path = '' as $$
  select jsonb_build_object(
    'status', coalesce(m.status, 'pending'),
    'allowed', coalesce(m.status = 'approved' and (m.expires_at is null or m.expires_at > now()), false),
    'expires_at', m.expires_at,
    'release_channel', coalesce(m.release_channel, 'private-beta'),
    'is_admin', exists(select 1 from dervo_private.admins a where a.user_id = (select auth.uid()))
  ) from (select auth.uid() as user_id) u
  left join public.dervo_memberships m on m.user_id = u.user_id;
$$;
revoke all on function public.dervo_access() from public, anon, authenticated;
grant execute on function public.dervo_access() to authenticated;

-- The first admin is bootstrapped by the project owner against a verified user ID.
-- Desktop callers carry their own session, never a service-role credential.
create function public.dervo_set_membership(target_user uuid, new_status text, expiry timestamptz default null)
returns void language plpgsql security definer set search_path = '' as $$
declare old_status text;
begin
  if not exists(select 1 from dervo_private.admins where user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  if new_status is null or new_status not in ('pending', 'approved', 'revoked') then
    raise exception 'Unsupported membership status' using errcode = '22023';
  end if;
  select status into old_status from public.dervo_memberships where user_id = target_user for update;
  if not found then
    raise exception 'Dervo account not found' using errcode = '22023';
  end if;
  update public.dervo_memberships set status = new_status, expires_at = expiry, updated_at = now()
    where user_id = target_user;
  insert into dervo_private.membership_audit(actor_id, subject_id, previous_status, new_status, expires_at)
    values (auth.uid(), target_user, old_status, new_status, expiry);
end;
$$;
revoke all on function public.dervo_set_membership(uuid, text, timestamptz) from public, anon, authenticated;
grant execute on function public.dervo_set_membership(uuid, text, timestamptz) to authenticated;

-- Avatars stay private. The client uses its own user ID as the first path segment.
insert into storage.buckets(id, name, public, file_size_limit, allowed_mime_types)
values ('dervo-avatars', 'dervo-avatars', false, 2097152, array['image/jpeg', 'image/png', 'image/webp']);
create policy dervo_avatar_read on storage.objects for select to authenticated
  using (bucket_id = 'dervo-avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'dervo-avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_update on storage.objects for update to authenticated
  using (bucket_id = 'dervo-avatars' and (storage.foldername(name))[1] = (select auth.uid())::text)
  with check (bucket_id = 'dervo-avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_delete on storage.objects for delete to authenticated
  using (bucket_id = 'dervo-avatars' and (storage.foldername(name))[1] = (select auth.uid())::text);

-- Existing permissive Storage policies are ORed together. These guards keep a
-- broader policy for another feature from opening Dervo's private avatar bucket.
create policy dervo_avatar_read_guard on storage.objects as restrictive for select to public
  using (bucket_id <> 'dervo-avatars' or (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_insert_guard on storage.objects as restrictive for insert to public
  with check (bucket_id <> 'dervo-avatars' or (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_update_guard on storage.objects as restrictive for update to public
  using (bucket_id <> 'dervo-avatars' or (storage.foldername(name))[1] = (select auth.uid())::text)
  with check (bucket_id <> 'dervo-avatars' or (storage.foldername(name))[1] = (select auth.uid())::text);
create policy dervo_avatar_delete_guard on storage.objects as restrictive for delete to public
  using (bucket_id <> 'dervo-avatars' or (storage.foldername(name))[1] = (select auth.uid())::text);

commit;
