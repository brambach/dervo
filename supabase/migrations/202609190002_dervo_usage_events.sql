-- Usage events from the private beta (beta.9, 19 Sep 2026). Apply only to the selected Dervo project, after review.
-- What a Mac sends: a fixed event name, a few enumerated properties, the app version and a random per-install id. Never
-- content: the app drops anything that isn't on its allowlist (backend/usage_events.py) before sending, and the checks
-- below are the server's own limits on top of that.
-- A signed-in user can only add rows, and only as themselves. user_id comes from auth.uid(): it isn't a column they're
-- granted, so a row that names one is refused, and the policy checks it again. Nobody reads the table directly. Admins
-- read daily totals through dervo_admin_usage(), which checks dervo_private.admins for the caller's own session first.
begin;

create table public.dervo_usage_events (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  install_id uuid not null,
  event text not null check (char_length(event) between 3 and 40 and event ~ '^[a-z][a-z_]+$'),
  properties jsonb not null default '{}'::jsonb check (
    jsonb_typeof(properties) = 'object'
    and octet_length(properties::text) <= 512
    -- Flat values only, and no long strings: every value the app sends is a short enumerated word or a number.
    and not jsonb_path_exists(properties, 'strict $.* ? (@.type() == "object" || @.type() == "array")')
    and not jsonb_path_exists(properties, 'strict $.* ? (@.type() == "string" && @ like_regex "^.{41}")')
  ),
  app_version text check (app_version ~ '^[A-Za-z0-9.+_-]{1,40}$'),
  created_at timestamptz not null default now()
);
create index dervo_usage_events_time on public.dervo_usage_events (created_at, event);

alter table public.dervo_usage_events enable row level security;
revoke all on public.dervo_usage_events from public, anon, authenticated;
grant insert (install_id, event, properties, app_version) on public.dervo_usage_events to authenticated;
create policy dervo_usage_insert on public.dervo_usage_events for insert to authenticated
  with check (user_id = (select auth.uid()));
-- No select, update or delete policy and no grant for them: a tester can't read back their own rows or anyone else's.

-- The Testers page's Usage section. One call, one document: active testers, daily counts per event and per screen,
-- the first-run funnel and task outcomes by provider. Counts only, never a row. Days are UTC.
create function public.dervo_admin_usage(days integer default 7)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  span integer := least(greatest(coalesce(dervo_admin_usage.days, 7), 1), 90);
  since timestamptz := date_trunc('day', now()) - make_interval(days => span - 1);
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'since', since,
    'days', span,
    'active', (select count(distinct e.user_id) from public.dervo_usage_events e where e.created_at >= since),
    -- Testers who opened Dervo, and everything they did, per day.
    'daily', coalesce((select jsonb_agg(jsonb_build_object('day', d.day, 'users', d.users, 'events', d.events) order by d.day)
      from (select (date_trunc('day', e.created_at))::date as day, count(distinct e.user_id) as users, count(*) as events
            from public.dervo_usage_events e where e.created_at >= since group by 1) d), '[]'::jsonb),
    'events', coalesce((select jsonb_agg(jsonb_build_object('day', d.day, 'event', d.event, 'users', d.users, 'count', d.n)
        order by d.day, d.event)
      from (select (date_trunc('day', e.created_at))::date as day, e.event, count(distinct e.user_id) as users, count(*) as n
            from public.dervo_usage_events e where e.created_at >= since group by 1, 2) d), '[]'::jsonb),
    'screens', coalesce((select jsonb_agg(jsonb_build_object('day', d.day, 'screen', d.screen, 'users', d.users, 'count', d.n)
        order by d.day, d.screen)
      from (select (date_trunc('day', e.created_at))::date as day, e.properties->>'screen' as screen,
                   count(distinct e.user_id) as users, count(*) as n
            from public.dervo_usage_events e
            where e.created_at >= since and e.event = 'screen_viewed' and e.properties->>'screen' ~ '^[a-z][a-z_.]{0,39}$'
            group by 1, 2) d), '[]'::jsonb),
    -- First run happens once per person, so the funnel counts everyone since usage sharing began, not just this window.
    'funnel', (select jsonb_agg(jsonb_build_object('step', s.step,
        'reached', (select count(distinct e.user_id) from public.dervo_usage_events e where e.event = 'first_run_step'
                    and e.properties->>'step' = s.step and e.properties->>'status' = 'reached'),
        'completed', (select count(distinct e.user_id) from public.dervo_usage_events e where e.event = 'first_run_step'
                      and e.properties->>'step' = s.step and e.properties->>'status' = 'completed')) order by s.n)
      from unnest(array['sign_in', 'pending', 'folder', 'start', 'project_created']) with ordinality as s(step, n)),
    'tasks', coalesce((select jsonb_agg(jsonb_build_object('provider', t.provider, 'outcome', t.outcome, 'count', t.n,
        'users', t.users, 'median_seconds', t.median_seconds) order by t.provider, t.outcome)
      from (select e.properties->>'provider' as provider, e.properties->>'outcome' as outcome, count(*) as n,
                   count(distinct e.user_id) as users,
                   round(percentile_cont(0.5) within group (order by case when jsonb_typeof(e.properties->'seconds') = 'number'
                     then (e.properties->>'seconds')::numeric end)::numeric) as median_seconds
            from public.dervo_usage_events e
            where e.created_at >= since and e.event = 'task_ended'
              and e.properties->>'provider' in ('claude', 'codex')
              and e.properties->>'outcome' in ('completed', 'failed', 'stopped')
            group by 1, 2) t), '[]'::jsonb)
  );
end;
$$;
revoke all on function public.dervo_admin_usage(integer) from public, anon, authenticated;
grant execute on function public.dervo_admin_usage(integer) to authenticated;

commit;
