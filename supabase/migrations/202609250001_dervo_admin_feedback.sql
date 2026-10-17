-- Tester reports in admin Dervo (beta.16, b16/lifecycle; Bryce, 24 Sep 2026). What testers send with Send feedback and
-- ⌘⇧B lands in dervo_private.beta_feedback, which nobody can read from the app. This adds one read for Dervo admins:
-- the last ``days`` days of reports, newest first, for the Tester reports section of Next up. Discord delivery and who
-- can write stay exactly as they are. Apply to the hosted project only with Bryce's go.
--
-- The page shows a first name, the build and the screen (design b16-lifecycle, 25 Sep), so that's all this returns:
-- the first word of the tester's Dervo display name (null when they haven't set one), never their email or user id,
-- and none of the tester's own project, task, conversation or session ids from the context.
begin;

create function public.dervo_admin_feedback(days integer default 30)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  span integer := least(greatest(coalesce(dervo_admin_feedback.days, 30), 1), 90);
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'days', span,
    'reports', coalesce((select jsonb_agg(jsonb_build_object(
        'id', r.id, 'category', r.category, 'message', r.message, 'created_at', r.created_at,
        'tester', r.tester, 'app_version', r.app_version, 'route', r.route, 'provider', r.provider) order by r.created_at desc)
      from (select f.id, f.category, f.message, f.created_at,
                   nullif(split_part(btrim(p.display_name), ' ', 1), '') as tester,
                   f.context->>'appVersion' as app_version, f.context->>'route' as route, f.context->>'provider' as provider
            from dervo_private.beta_feedback f
            left join public.dervo_profiles p on p.user_id = f.user_id
            where f.created_at >= now() - make_interval(days => span)
            order by f.created_at desc
            limit 200) r), '[]'::jsonb));
end;
$$;
revoke all on function public.dervo_admin_feedback(integer) from public, anon, authenticated;
grant execute on function public.dervo_admin_feedback(integer) to authenticated;

commit;
