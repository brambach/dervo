-- Beta invitations (22 Sep 2026). An admin invites a waitlist email; the first verified sign-in with that email is
-- approved without anyone editing a row. The website Worker (website/server/invitations.mjs) calls these with the
-- admin's own session, then sends the email. Nothing here sends mail or holds a credential.
-- One row per invited email, normalised (trimmed, lowercased). invite_id is the idempotency key the Worker hands to the
-- mail provider: asking again for a live invitation returns the same ID, and only an explicit resend rotates it.
-- Every function checks dervo_private.admins for the caller first, runs with an empty search_path and can be called
-- only by signed-in users. Membership changes are written to the existing membership audit, invitation changes to
-- their own. Apply only to the selected Dervo project, after review.
begin;

create table dervo_private.invitations (
  email text primary key check (email = lower(btrim(email)) and char_length(email) between 3 and 254),
  invite_id uuid not null unique default gen_random_uuid(),
  status text not null default 'invited' check (status in ('invited', 'accepted', 'revoked')),
  invited_by uuid not null,
  invited_at timestamptz not null default now(),
  expires_at timestamptz not null,
  accepted_user uuid,
  accepted_at timestamptz
);
create table dervo_private.invitation_audit (
  id bigint generated always as identity primary key,
  actor_id uuid not null,
  email text not null,
  invite_id uuid not null,
  action text not null check (action in ('invited', 'renewed', 'revoked', 'accepted')),
  changed_at timestamptz not null default now()
);
alter table dervo_private.invitations enable row level security;
alter table dervo_private.invitation_audit enable row level security;
revoke all on dervo_private.invitations, dervo_private.invitation_audit from public, anon, authenticated;

-- Invite one email, or return the invitation it already has. A confirmed account that's still pending is approved
-- here, by this admin. An approved or revoked member is reported and left alone: the Testers page changes those.
create function public.dervo_admin_invite(invite_email text, resend boolean default false) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  address text := lower(btrim(coalesce(invite_email, '')));
  invitation dervo_private.invitations%rowtype;
  had_invitation boolean;
  member_id uuid;
  member_status text;
  step text;
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = caller) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  if char_length(address) not between 3 and 254 or address !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception 'Unsupported email address' using errcode = '22023';
  end if;
  -- Two requests for the same email take turns, so they can't mint two IDs.
  perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(address, 0));

  select m.user_id, m.status into member_id, member_status
    from auth.users u join public.dervo_memberships m on m.user_id = u.id
    where lower(u.email) = address and u.email_confirmed_at is not null
    order by u.created_at limit 1 for update of m;
  select * into invitation from dervo_private.invitations i where i.email = address for update;
  had_invitation := found;
  if member_status = 'approved' then
    -- A failed email can still retry the same invitation after approval. This doesn't change membership.
    return jsonb_build_object('state', 'already_approved',
      'invite_id', case when invitation.status = 'accepted' and invitation.expires_at > now() then invitation.invite_id end,
      'expires_at', case when invitation.status = 'accepted' and invitation.expires_at > now() then invitation.expires_at end);
  elsif member_status = 'revoked' then
    return jsonb_build_object('state', 'member_revoked', 'invite_id', null, 'expires_at', null);
  end if;

  if had_invitation and invitation.status = 'invited' and invitation.expires_at > now() and not coalesce(resend, false) then
    return jsonb_build_object('state', 'existing', 'invite_id', invitation.invite_id, 'expires_at', invitation.expires_at);
  end if;
  step := case when had_invitation then 'renewed' else 'invited' end;
  insert into dervo_private.invitations as i (email, invited_by, expires_at)
    values (address, caller, now() + interval '14 days')
    on conflict (email) do update set invite_id = gen_random_uuid(), status = 'invited', invited_by = caller,
      invited_at = now(), expires_at = now() + interval '14 days', accepted_user = null, accepted_at = null
    returning i.* into invitation;
  insert into dervo_private.invitation_audit(actor_id, email, invite_id, action)
    values (caller, address, invitation.invite_id, step);

  if member_status = 'pending' then
    update public.dervo_memberships set status = 'approved', expires_at = null, updated_at = now() where user_id = member_id;
    insert into dervo_private.membership_audit(actor_id, subject_id, previous_status, new_status, expires_at)
      values (caller, member_id, 'pending', 'approved', null);
    update dervo_private.invitations set status = 'accepted', accepted_user = member_id, accepted_at = now()
      where email = address;
    insert into dervo_private.invitation_audit(actor_id, email, invite_id, action)
      values (caller, address, invitation.invite_id, 'accepted');
  end if;
  return jsonb_build_object('state', case when had_invitation then 'renewed' else 'created' end,
                            'invite_id', invitation.invite_id, 'expires_at', invitation.expires_at);
end;
$$;
revoke all on function public.dervo_admin_invite(text, boolean) from public, anon, authenticated;
grant execute on function public.dervo_admin_invite(text, boolean) to authenticated;

create function public.dervo_admin_invitations()
returns table (email text, invite_id uuid, status text, invited_at timestamptz, expires_at timestamptz, accepted_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return query
    select i.email, i.invite_id, i.status, i.invited_at, i.expires_at, i.accepted_at
    from dervo_private.invitations i
    order by i.invited_at desc, i.email
    limit 1000;
end;
$$;
revoke all on function public.dervo_admin_invitations() from public, anon, authenticated;
grant execute on function public.dervo_admin_invitations() to authenticated;

-- Withdraw an invitation nobody has used yet. An accepted one is a membership now: revoke that on the Testers page.
create function public.dervo_admin_revoke_invitation(invite_email text) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  caller uuid := (select auth.uid());
  address text := lower(btrim(coalesce(invite_email, '')));
  invitation dervo_private.invitations%rowtype;
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = caller) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  select * into invitation from dervo_private.invitations i where i.email = address for update;
  if not found then
    return jsonb_build_object('state', 'not_found');
  elsif invitation.status = 'accepted' then
    return jsonb_build_object('state', 'accepted');
  elsif invitation.status = 'invited' then
    update dervo_private.invitations set status = 'revoked' where email = address;
    insert into dervo_private.invitation_audit(actor_id, email, invite_id, action)
      values (caller, address, invitation.invite_id, 'revoked');
  end if;
  return jsonb_build_object('state', 'revoked');
end;
$$;
revoke all on function public.dervo_admin_revoke_invitation(text) from public, anon, authenticated;
grant execute on function public.dervo_admin_revoke_invitation(text) to authenticated;

-- The invited email's first verified sign-in is approved, with the inviting admin as the audit's actor. Only a pending
-- membership moves, so an invitation never undoes a revocation. Sign-in must never fail because of this trigger, so
-- any error here leaves the account pending instead.
create function dervo_private.accept_invitation() returns trigger
language plpgsql security definer set search_path = '' as $$
declare invitation dervo_private.invitations%rowtype;
begin
  if new.email is null or new.email_confirmed_at is null then
    return new;
  end if;
  begin
    select * into invitation from dervo_private.invitations i
      where i.email = lower(btrim(new.email)) and i.status = 'invited' and i.expires_at > now() for update;
    if not found then
      return new;
    end if;
    update public.dervo_memberships set status = 'approved', expires_at = null, updated_at = now()
      where user_id = new.id and status = 'pending';
    if not found then
      return new;
    end if;
    insert into dervo_private.membership_audit(actor_id, subject_id, previous_status, new_status, expires_at)
      values (invitation.invited_by, new.id, 'pending', 'approved', null);
    update dervo_private.invitations set status = 'accepted', accepted_user = new.id, accepted_at = now()
      where email = invitation.email;
    insert into dervo_private.invitation_audit(actor_id, email, invite_id, action)
      values (invitation.invited_by, invitation.email, invitation.invite_id, 'accepted');
  exception when others then
    return new;
  end;
  return new;
end;
$$;
revoke all on function dervo_private.accept_invitation() from public, anon, authenticated;
-- Named to sort after dervo_new_user, which creates the pending membership this one approves.
create trigger dervo_user_invited after insert or update of email_confirmed_at on auth.users
  for each row execute function dervo_private.accept_invitation();

commit;
