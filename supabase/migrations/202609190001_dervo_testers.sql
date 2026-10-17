-- Testers, in Dervo's Settings, for admins only (design sweep, 19 Sep 2026).
-- Two read functions for the page: who has a membership (with their email and when they signed up), and the membership
-- audit that dervo_set_membership already writes. Changes still go only through dervo_set_membership, which checks the
-- caller and audits every change. Nothing here writes.
-- Each function runs as its owner so it can read auth.users and dervo_private, checks that the caller's own session is
-- in dervo_private.admins before reading anything, uses an empty search_path with every name qualified, and can be
-- called only by signed-in users. Apply only to the selected Dervo project, after review.
begin;

create function public.dervo_admin_memberships()
returns table (user_id uuid, email text, status text, expires_at timestamptz, signed_up_at timestamptz,
               updated_at timestamptz, is_admin boolean)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return query
    select m.user_id, u.email::text, m.status, m.expires_at, u.created_at, m.updated_at,
           exists (select 1 from dervo_private.admins x where x.user_id = m.user_id)
    from public.dervo_memberships m
    join auth.users u on u.id = m.user_id
    order by u.created_at desc, m.user_id
    limit 500;
end;
$$;
revoke all on function public.dervo_admin_memberships() from public, anon, authenticated;
grant execute on function public.dervo_admin_memberships() to authenticated;

-- Newest first, a page at a time: rows older than before_id when it's given, at most 100 per call.
create function public.dervo_admin_membership_audit(before_id bigint default null, max_rows integer default 20)
returns table (id bigint, changed_at timestamptz, actor_id uuid, actor_email text, subject_id uuid, subject_email text,
               previous_status text, new_status text, expires_at timestamptz)
language plpgsql stable security definer set search_path = '' as $$
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return query
    select l.id, l.changed_at, l.actor_id, actor.email::text, l.subject_id, subject.email::text,
           l.previous_status, l.new_status, l.expires_at
    from dervo_private.membership_audit l
    left join auth.users actor on actor.id = l.actor_id
    left join auth.users subject on subject.id = l.subject_id
    where dervo_admin_membership_audit.before_id is null or l.id < dervo_admin_membership_audit.before_id
    order by l.id desc
    limit least(greatest(coalesce(dervo_admin_membership_audit.max_rows, 20), 1), 100);
end;
$$;
revoke all on function public.dervo_admin_membership_audit(bigint, integer) from public, anon, authenticated;
grant execute on function public.dervo_admin_membership_audit(bigint, integer) to authenticated;

commit;
