-- Security review L2 (23 Sep 2026): feedback, usage and error rows had no per-person limit, and usage and error rows
-- didn't need approval. Prepared for approval. Another session applies it after Bryce has seen the diff; nothing here
-- was run against the hosted project. Offline checks: supabase/tests/run_local.py (feedback_limit.sql, and the approved
-- and pending cases in usage_events.sql and error_reports.sql).
--
-- 1. dervo_submit_feedback refuses a caller's 21st new submission within an hour. Every accepted row still goes to
--    the Discord delivery trigger from 202609160002 unchanged, and a refused one never reaches the table, so it never
--    reaches Discord. The refusal is SQLSTATE PT429, which PostgREST answers as HTTP 429. The app's client already
--    reads 429 as "Please wait before trying again" and shows its usual feedback error. Same body as 202609210001
--    otherwise.
-- 2. The insert policies on dervo_usage_events and dervo_error_reports also need an approved, unexpired membership,
--    the check dervo_release_read uses (202609150001) without its channel, since these rows have none. The app's
--    inserts from approved testers pass as before. What changes: rows from a signed-in tester who's still pending are
--    refused. The app sends those as a batch and drops a refused batch without retrying (backend/usage_events.py), so
--    the Testers page's first-run funnel no longer counts the sign_in and "pending reached" steps a waitlisted tester
--    reaches before approval, and errors from before approval aren't reported.
--
-- Manual check after it's applied, as an admin in the SQL editor (reads only):
--   select policyname, with_check from pg_policies
--    where tablename in ('dervo_usage_events', 'dervo_error_reports');   -- both name dervo_memberships and 'approved'
--   select position('PT429' in prosrc) > 0 from pg_proc where proname = 'dervo_submit_feedback';   -- true
--   select user_id, count(*) from dervo_private.beta_feedback
--    where created_at > now() - interval '1 hour' group by 1 order by 2 desc limit 5;   -- nobody past 20 from now on
begin;

create index if not exists beta_feedback_user_time on dervo_private.beta_feedback (user_id, created_at);

create or replace function public.dervo_submit_feedback(submission jsonb) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  owner uuid := auth.uid();
  request_id uuid;
  ctx jsonb;
  entry record;
  saved dervo_private.beta_feedback;
begin
  if owner is null or not exists(select 1 from auth.users where id = owner and email_confirmed_at is not null and not is_anonymous) then
    raise exception 'Confirmed Dervo identity required' using errcode = '42501';
  end if;
  perform 1 from public.dervo_memberships where user_id = owner and status = 'approved'
    and (expires_at is null or expires_at > now()) for share;
  if not found then
    raise exception 'Approved Dervo beta access required' using errcode = '42501';
  end if;
  if jsonb_typeof(submission) is distinct from 'object' or
     not submission ?& array['request_id','category','message','context'] or
     submission - array['request_id','category','message','context'] <> '{}'::jsonb then
    raise exception 'Unsupported feedback fields' using errcode = '22023';
  end if;
  if jsonb_typeof(submission->'request_id') is distinct from 'string' or
     jsonb_typeof(submission->'category') is distinct from 'string' or
     submission->>'category' not in ('feedback','bug','confusing','idea') or
     jsonb_typeof(submission->'message') is distinct from 'string' or
     char_length(submission->>'message') not between 1 and 4000 or
     btrim(submission->>'message') = '' then
    raise exception 'Invalid feedback' using errcode = '22023';
  end if;
  request_id := (submission->>'request_id')::uuid;
  ctx := submission->'context';
  if jsonb_typeof(ctx) is distinct from 'object' or not ctx ? 'route' or
     ctx - array['route','projectId','taskId','conversationId','sessionId','provider','appVersion','viewport'] <> '{}'::jsonb then
    raise exception 'Unsupported feedback context' using errcode = '22023';
  end if;
  for entry in select key, value from jsonb_each(ctx) loop
    if entry.key = 'viewport' then
      if jsonb_typeof(entry.value) is distinct from 'object' or
         not entry.value ?& array['width','height'] or entry.value - array['width','height'] <> '{}'::jsonb or
         jsonb_typeof(entry.value->'width') is distinct from 'number' or
         jsonb_typeof(entry.value->'height') is distinct from 'number' or
         (entry.value->>'width') !~ '^[0-9]{1,5}$' or (entry.value->>'height') !~ '^[0-9]{1,5}$' or
         (entry.value->>'width')::integer not between 1 and 32768 or
         (entry.value->>'height')::integer not between 1 and 32768 then
        raise exception 'Invalid feedback viewport' using errcode = '22023';
      end if;
    else
      if jsonb_typeof(entry.value) is distinct from 'string' then
        raise exception 'Invalid feedback context value' using errcode = '22023';
      end if;
      if (entry.key = 'route' and entry.value #>> '{}' not in ('conversation','catch-up','search','knowledge','settings','welcome','workspace')) or
         (entry.key = 'provider' and entry.value #>> '{}' not in ('codex','claude','openrouter')) or
         (entry.key = 'appVersion' and entry.value #>> '{}' !~ '^[A-Za-z0-9.+_-]{1,80}$') or
         (entry.key in ('projectId','taskId','conversationId','sessionId') and entry.value #>> '{}' !~ '^[A-Za-z0-9_:-]{1,128}$') then
        raise exception 'Invalid feedback context value' using errcode = '22023';
      end if;
    end if;
  end loop;
  -- Twenty a person an hour (security review L2). The count is this caller's own saved rows, taken under a lock held
  -- for the caller alone, so parallel calls can't all read nineteen. A retry of a request that's already saved isn't a
  -- new submission: it passes and returns its receipt as before.
  perform pg_advisory_xact_lock(hashtextextended('dervo_submit_feedback:' || owner::text, 0));
  if not exists(select 1 from dervo_private.beta_feedback where user_id = owner and id = request_id) and
     (select count(*) from dervo_private.beta_feedback
      where user_id = owner and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'Feedback limit reached. Try again in an hour.' using errcode = 'PT429';
  end if;
  insert into dervo_private.beta_feedback(user_id,id,category,message,context)
    values(owner,request_id,submission->>'category',submission->>'message',ctx)
    on conflict(user_id,id) do nothing;
  select * into strict saved from dervo_private.beta_feedback where user_id = owner and id = request_id;
  if saved.category <> submission->>'category' or saved.message <> submission->>'message' or saved.context <> ctx then
    raise exception 'Feedback request ID already used' using errcode = '22023';
  end if;
  return jsonb_build_object('id',saved.id,'created_at',saved.created_at);
end;
$$;

revoke all on function public.dervo_submit_feedback(jsonb) from public, anon, authenticated;
grant execute on function public.dervo_submit_feedback(jsonb) to authenticated;

-- A row as yourself, and only while your beta access is approved and unexpired.
drop policy dervo_usage_insert on public.dervo_usage_events;
create policy dervo_usage_insert on public.dervo_usage_events for insert to authenticated
  with check (user_id = (select auth.uid()) and exists (
    select 1 from public.dervo_memberships m
    where m.user_id = (select auth.uid()) and m.status = 'approved' and (m.expires_at is null or m.expires_at > now())
  ));
drop policy dervo_error_insert on public.dervo_error_reports;
create policy dervo_error_insert on public.dervo_error_reports for insert to authenticated
  with check (user_id = (select auth.uid()) and exists (
    select 1 from public.dervo_memberships m
    where m.user_id = (select auth.uid()) and m.status = 'approved' and (m.expires_at is null or m.expires_at > now())
  ));

commit;
