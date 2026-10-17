-- Founder testers (Bryce, 24 Sep 2026; beta.15, group 7): everyone approved before the public launch gets Obsidian, the
-- founder app icon, and it stays with their account, so a reinstall or a new Mac keeps it (backend/app_icons.py reads
-- dervo_is_founder()). A founder row is written once and no later status change removes it.
-- Founders are recorded while the founder window is open. Closing it at launch is one statement, below, not part of
-- this migration. dervo_access() is unchanged, because older apps refuse an answer with extra fields.
-- Applying this to the hosted project needs Bryce's go, like 202609240001_dervo_admin_previews.sql.
begin;

create table dervo_private.founders (
  user_id uuid primary key references auth.users(id) on delete cascade,
  granted_at timestamptz not null default now()
);
alter table dervo_private.founders enable row level security;
revoke all on dervo_private.founders from public, anon, authenticated;

-- One row: when the founder window closed, or null while it's open (before the public launch).
create table dervo_private.founder_window (
  id boolean primary key default true check (id),
  closed_at timestamptz
);
alter table dervo_private.founder_window enable row level security;
revoke all on dervo_private.founder_window from public, anon, authenticated;
insert into dervo_private.founder_window(id, closed_at) values (true, null);

-- Everyone approved today is a founder.
insert into dervo_private.founders(user_id)
  select user_id from public.dervo_memberships where status = 'approved'
  on conflict (user_id) do nothing;

-- Anyone approved later while the window is open becomes one too, however they were approved: by an admin, by an
-- invitation, or when an invited person signs in.
create function dervo_private.record_founder() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.status = 'approved' and exists(
      select 1 from dervo_private.founder_window w where w.closed_at is null or w.closed_at > now()) then
    insert into dervo_private.founders(user_id) values (new.user_id) on conflict (user_id) do nothing;
  end if;
  return new;
end;
$$;
revoke all on function dervo_private.record_founder() from public, anon, authenticated;
create trigger dervo_record_founder after insert or update of status on public.dervo_memberships
  for each row execute function dervo_private.record_founder();

-- Whether the signed-in caller is a founder. Callers can't read dervo_private, so this reads it as its owner with an
-- empty search_path and every name qualified, like dervo_is_admin().
create function public.dervo_is_founder() returns boolean
language sql stable security definer set search_path = '' as $$
  select exists(select 1 from dervo_private.founders f where f.user_id = (select auth.uid()));
$$;
revoke all on function public.dervo_is_founder() from public, anon, authenticated;
grant execute on function public.dervo_is_founder() to authenticated;

commit;

-- At the public launch, with Bryce's go, close the window in the project's SQL editor:
--   update dervo_private.founder_window set closed_at = now();
