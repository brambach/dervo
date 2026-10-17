-- Apply only after reviewing the Worker deployment and configuring Vault.
-- pg_net and Supabase Vault must already be enabled in the selected project.
begin;

create table dervo_private.feedback_discord_delivery (
  user_id uuid not null,
  feedback_id uuid not null,
  request_id bigint,
  attempted_at timestamptz not null default now(),
  state text not null check (state in ('queued','not_configured','enqueue_failed')),
  primary key (user_id, feedback_id),
  foreign key (user_id, feedback_id) references dervo_private.beta_feedback(user_id,id) on delete cascade
);
alter table dervo_private.feedback_discord_delivery enable row level security;
revoke all on dervo_private.feedback_discord_delivery from public, anon, authenticated;

-- Operators can replay an exact saved record. This isn't a client RPC or a queue.
create function dervo_private.notify_feedback_discord(owner_id uuid, record_id uuid) returns void
language plpgsql security definer set search_path = '' as $$
declare
  saved dervo_private.beta_feedback;
  endpoint text;
  credential text;
  request_number bigint;
  safe_context jsonb;
  delivery_state text := 'not_configured';
begin
  select * into saved from dervo_private.beta_feedback where user_id = owner_id and id = record_id;
  if not found then return; end if;
  begin
    select decrypted_secret into endpoint from vault.decrypted_secrets where name = 'dervo_feedback_worker_url';
    select decrypted_secret into credential from vault.decrypted_secrets where name = 'dervo_feedback_worker_secret';
    if endpoint ~ '^https://dervo-latch-worker\.[a-z0-9-]+\.workers\.dev/internal/feedback$'
       and credential ~ '^[a-f0-9]{64}$' then
      select coalesce(jsonb_object_agg(key,value), '{}'::jsonb) into safe_context
        from jsonb_each(saved.context)
        where key in ('route','projectId','taskId','conversationId','provider','appVersion');
      -- pg_net sends only after this transaction commits. No remote wait in the form.
      select net.http_post(
        url := endpoint,
        headers := jsonb_build_object('Content-Type','application/json','Authorization','Bearer ' || credential),
        body := jsonb_build_object('id',saved.id,'user_id',saved.user_id,'category',saved.category,
                                  'message',saved.message,'created_at',saved.created_at,'context',safe_context),
        timeout_milliseconds := 5000
      ) into request_number;
      delivery_state := 'queued';
    end if;
  exception when others then
    -- Never emit SQLERRM, HTTP bodies, credentials or a stack trace.
    delivery_state := 'enqueue_failed';
  end;
  insert into dervo_private.feedback_discord_delivery(user_id,feedback_id,request_id,attempted_at,state)
    values(owner_id,record_id,request_number,clock_timestamp(),delivery_state)
    on conflict (user_id,feedback_id) do update set
      request_id = excluded.request_id, attempted_at = excluded.attempted_at, state = excluded.state;
exception when others then
  raise log 'Dervo feedback notification bookkeeping failed for record %', record_id;
end;
$$;
revoke all on function dervo_private.notify_feedback_discord(uuid,uuid) from public, anon, authenticated;

create function dervo_private.feedback_discord_after_insert() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  perform dervo_private.notify_feedback_discord(new.user_id,new.id);
  return new;
exception when others then
  raise log 'Dervo feedback notification skipped for record %', new.id;
  return new;
end;
$$;
revoke all on function dervo_private.feedback_discord_after_insert() from public, anon, authenticated;
create trigger feedback_discord_after_insert after insert on dervo_private.beta_feedback
  for each row execute function dervo_private.feedback_discord_after_insert();
commit;
