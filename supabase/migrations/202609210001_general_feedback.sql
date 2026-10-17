-- Prepared for approval. Accept general feedback through the existing delivery trigger.
begin;
alter table dervo_private.beta_feedback drop constraint beta_feedback_category_check;
alter table dervo_private.beta_feedback add constraint beta_feedback_category_check check (category in ('feedback','bug','confusing','idea'));
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
commit;
