create function pg_temp.assert_true(value boolean, label text) returns void language plpgsql as $$
begin if value is distinct from true then raise exception 'Assertion failed: %', label; end if; end;
$$;
create function pg_temp.denied(statement text) returns void language plpgsql as $$
begin
  begin execute statement; exception when insufficient_privilege then return; end;
  raise exception 'Expected permission denial';
end;
$$;
insert into auth.users(id) values ('00000000-0000-0000-0000-000000000090');
update public.dervo_memberships set status = 'approved', expires_at = null
 where user_id = '00000000-0000-0000-0000-000000000090';
set role authenticated;
select set_config('request.jwt.claim.sub','00000000-0000-0000-0000-000000000090',false);
select pg_temp.denied('select * from dervo_private.feedback_discord_delivery');
select pg_temp.denied($q$select dervo_private.notify_feedback_discord('00000000-0000-0000-0000-000000000090','00000000-0000-0000-0000-000000000001')$q$);
select pg_temp.assert_true(public.dervo_submit_feedback('{
 "request_id":"00000000-0000-0000-0000-000000000001","category":"bug","message":"Saved without configuration",
 "context":{"route":"settings"}}')->>'id'='00000000-0000-0000-0000-000000000001','unconfigured save succeeds');
reset role;
select pg_temp.assert_true((select state = 'not_configured' from dervo_private.feedback_discord_delivery
 where feedback_id='00000000-0000-0000-0000-000000000001'),'missing config recorded');
insert into vault.decrypted_secrets values
 ('dervo_feedback_worker_url','https://dervo-latch-worker.test.workers.dev/internal/feedback'),
 ('dervo_feedback_worker_secret',repeat('a',64));
set role authenticated;
select public.dervo_submit_feedback('{
 "request_id":"00000000-0000-0000-0000-000000000002","category":"idea","message":"Synthetic feature suggestion",
 "context":{"route":"conversation","projectId":"trial","taskId":"task1","conversationId":"chat1","provider":"claude","appVersion":"0.1.0","sessionId":"not-forwarded","viewport":{"width":1200,"height":800}}}');
-- A client retry doesn't enqueue a second notification.
select public.dervo_submit_feedback('{
 "request_id":"00000000-0000-0000-0000-000000000002","category":"idea","message":"Synthetic feature suggestion",
 "context":{"route":"conversation","projectId":"trial","taskId":"task1","conversationId":"chat1","provider":"claude","appVersion":"0.1.0","sessionId":"not-forwarded","viewport":{"width":1200,"height":800}}}');
reset role;
select pg_temp.assert_true((select count(*)=1 from net.test_requests),'duplicate save did not notify');
select pg_temp.assert_true((select body->'context' = '{"route":"conversation","projectId":"trial","taskId":"task1","conversationId":"chat1","provider":"claude","appVersion":"0.1.0"}' from net.test_requests),'only allowed context');
select pg_temp.assert_true((select (body - array['id','user_id','category','message','context','created_at']) = '{}' from net.test_requests),'only allowed top fields');
-- Simulate an enqueue failure, then a bookkeeping failure. Both saves must succeed.
select set_config('test.net_fails','yes',false);
set role authenticated;
select public.dervo_submit_feedback('{"request_id":"00000000-0000-0000-0000-000000000003","category":"confusing","message":"Saved despite notification failure","context":{"route":"settings"}}');
reset role;
select pg_temp.assert_true((select state='enqueue_failed' from dervo_private.feedback_discord_delivery where feedback_id='00000000-0000-0000-0000-000000000003'),'failure recorded');
select set_config('test.net_fails','no',false);
alter table dervo_private.feedback_discord_delivery rename to unavailable_delivery;
set role authenticated;
select public.dervo_submit_feedback('{"request_id":"00000000-0000-0000-0000-000000000004","category":"bug","message":"Saved despite bookkeeping failure","context":{"route":"settings"}}');
reset role;
alter table dervo_private.unavailable_delivery rename to feedback_discord_delivery;
select pg_temp.assert_true((select count(*)=4 from dervo_private.beta_feedback where user_id='00000000-0000-0000-0000-000000000090'),'all four submissions persisted');
-- Operator replay selects the original record, never accepts an arbitrary replacement body.
select dervo_private.notify_feedback_discord('00000000-0000-0000-0000-000000000090','00000000-0000-0000-0000-000000000003');
select pg_temp.assert_true((select state='queued' from dervo_private.feedback_discord_delivery where feedback_id='00000000-0000-0000-0000-000000000003'),'operator replay queued');
-- Rollback prevents persistence of either feedback or its queued notification.
begin;
insert into dervo_private.beta_feedback(user_id,id,category,message,context)
 values('00000000-0000-0000-0000-000000000090','00000000-0000-0000-0000-000000000005','bug','Rolled back','{"route":"settings"}');
rollback;
select pg_temp.assert_true(not exists(select 1 from net.test_requests where body->>'id'='00000000-0000-0000-0000-000000000005'),'rollback removes queued request');
set role authenticated;
select public.dervo_submit_feedback('{"request_id":"00000000-0000-0000-0000-000000000006","category":"feedback","message":"I love catch-up","context":{"route":"workspace"}}');
reset role;
select pg_temp.assert_true(exists(select 1 from net.test_requests where body->>'id'='00000000-0000-0000-0000-000000000006' and body->>'category'='feedback'),'general feedback uses the same delivery path');
select 'PASS: feedback persistence, permissions, allowlist, retries and failed notification isolation';
