-- Usage per tester, for the Testers page's People section (28 Sep 2026). dervo_admin_usage only returns totals, so it
-- can't say whether anyone besides the admin is doing anything. This reads the same rows, grouped by person.
-- Nothing new is collected: every usage row already carries its user_id, and the usage sharing disclosure says each
-- event goes with "your Dervo account". The email comes from auth.users, as dervo_admin_memberships already shows it.
-- Counts only, never a row's properties beyond the fixed words below. Admins only, checked against the caller's own
-- session. Apply only to the selected Dervo project, after review.
begin;

create function public.dervo_admin_usage_people(days integer default 7)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  span integer := least(greatest(coalesce(dervo_admin_usage_people.days, 7), 1), 90);
  since timestamptz := date_trunc('day', now()) - make_interval(days => span - 1);
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'since', since,
    'days', span,
    -- Everyone with approved or pending access, and anyone else who sent a row in the window, most recently seen first.
    -- A tester with access who never sent a row shows with zero counts and no last_seen: that's the "not using it" list.
    'people', coalesce((select jsonb_agg(to_jsonb(p) order by p.last_seen desc nulls last, p.email, p.user_id)
      from (
        select who.user_id, u.email::text as email, m.status,
               exists (select 1 from dervo_private.admins x where x.user_id = who.user_id) as is_admin,
               (select max(e.created_at) from public.dervo_usage_events e where e.user_id = who.user_id) as last_seen,
               (select e.app_version from public.dervo_usage_events e where e.user_id = who.user_id and e.app_version is not null
                order by e.created_at desc, e.id desc limit 1) as app_version,
               coalesce(w.active_days, 0) as active_days, coalesce(w.opens, 0) as opens,
               coalesce(w.threads, 0) as threads, coalesce(w.home_requests, 0) as home_requests,
               coalesce(w.tasks_started, 0) as tasks_started, coalesce(w.tasks_completed, 0) as tasks_completed,
               coalesce(w.tasks_failed, 0) as tasks_failed, coalesce(w.claude_tasks, 0) as claude_tasks,
               coalesce(w.codex_tasks, 0) as codex_tasks, coalesce(w.knowledge, 0) as knowledge,
               coalesce(w.feedback_opened, 0) as feedback_opened
        from (select m.user_id from public.dervo_memberships m where m.status in ('approved', 'pending')
              union
              select e.user_id from public.dervo_usage_events e where e.created_at >= since) who
        join auth.users u on u.id = who.user_id
        left join public.dervo_memberships m on m.user_id = who.user_id
        left join (
          select e.user_id,
                 count(distinct date_trunc('day', e.created_at)) as active_days,
                 count(*) filter (where e.event = 'app_opened') as opens,
                 count(*) filter (where e.event = 'thread_created') as threads,
                 count(*) filter (where e.event = 'home_request') as home_requests,
                 count(*) filter (where e.event = 'task_started') as tasks_started,
                 count(*) filter (where e.event = 'task_ended' and e.properties->>'outcome' = 'completed') as tasks_completed,
                 count(*) filter (where e.event = 'task_ended' and e.properties->>'outcome' = 'failed') as tasks_failed,
                 count(*) filter (where e.event = 'task_started' and e.properties->>'provider' = 'claude') as claude_tasks,
                 count(*) filter (where e.event = 'task_started' and e.properties->>'provider' = 'codex') as codex_tasks,
                 count(*) filter (where e.event in ('knowledge_rule_added', 'lesson_approved')) as knowledge,
                 count(*) filter (where e.event = 'feedback_opened') as feedback_opened
          from public.dervo_usage_events e where e.created_at >= since group by e.user_id) w on w.user_id = who.user_id
        order by 5 desc nulls last, 2, 1
        limit 500) p), '[]'::jsonb)
  );
end;
$$;
revoke all on function public.dervo_admin_usage_people(integer) from public, anon, authenticated;
grant execute on function public.dervo_admin_usage_people(integer) to authenticated;

commit;
