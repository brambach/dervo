-- Feedback's hourly limit (migrations/202609230001_feedback_limit_and_approved_rows.sql), security review L2. Runs after
-- feedback_discord.sql on the same isolated cluster, so the Discord stand-in (net.test_requests) is configured. Not
-- checked here: how hosted PostgREST answers SQLSTATE PT429 (it should be HTTP 429).
create function pg_temp.assert_true(value boolean, label text) returns void language plpgsql as $$
begin if value is distinct from true then raise exception 'Assertion failed: %', label; end if; end;
$$;
create function pg_temp.limited(statement text) returns void language plpgsql as $$
begin
  begin execute statement; exception when sqlstate 'PT429' then return; end;
  raise exception 'Expected the hourly feedback limit: %', statement;
end;
$$;
create function pg_temp.send(n integer) returns jsonb language sql as $$
  select public.dervo_submit_feedback(jsonb_build_object('request_id', format('00000000-0000-0000-0000-%s', lpad(n::text, 12, '0')),
    'category', 'bug', 'message', 'Limit check ' || n, 'context', jsonb_build_object('route', 'settings')))
$$;

insert into auth.users(id) values ('00000000-0000-0000-0000-000000000091'), ('00000000-0000-0000-0000-000000000092');
update public.dervo_memberships set status = 'approved', expires_at = null
 where user_id in ('00000000-0000-0000-0000-000000000091', '00000000-0000-0000-0000-000000000092');

set role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000091', false);
-- Twenty in an hour are accepted.
do $$ begin for n in 101..120 loop perform pg_temp.send(n); end loop; end $$;
-- The twenty-first is refused with the limit's own code, and so is the one after.
select pg_temp.limited('select pg_temp.send(121)');
select pg_temp.limited('select pg_temp.send(122)');
-- A retry of a request that's already saved isn't new: it still returns its receipt.
select pg_temp.assert_true(pg_temp.send(120)->>'id' = '00000000-0000-0000-0000-000000000120', 'a retry past the limit keeps its receipt');
select pg_temp.assert_true(pg_temp.send(101)->>'id' = '00000000-0000-0000-0000-000000000101', 'so does the first');
-- The limit is per person: someone else's feedback goes through.
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000092', false);
select pg_temp.assert_true(pg_temp.send(201)->>'id' = '00000000-0000-0000-0000-000000000201', 'another tester is unaffected');
reset role;

select pg_temp.assert_true((select count(*) = 20 from dervo_private.beta_feedback
  where user_id = '00000000-0000-0000-0000-000000000091'), 'exactly twenty kept');
select pg_temp.assert_true(not exists(select 1 from dervo_private.beta_feedback
  where id in ('00000000-0000-0000-0000-000000000121', '00000000-0000-0000-0000-000000000122')), 'a refused one is never saved');
-- Discord delivery is unchanged for accepted rows, once each, and a refused one never gets there.
select pg_temp.assert_true((select count(*) = 20 from net.test_requests
  where body->>'user_id' = '00000000-0000-0000-0000-000000000091'), 'every accepted row went to Discord once');
select pg_temp.assert_true(not exists(select 1 from net.test_requests
  where body->>'id' in ('00000000-0000-0000-0000-000000000121', '00000000-0000-0000-0000-000000000122')), 'a refused one never did');

-- An hour on, there's room again.
update dervo_private.beta_feedback set created_at = now() - interval '61 minutes'
 where user_id = '00000000-0000-0000-0000-000000000091';
set role authenticated;
select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000091', false);
select pg_temp.assert_true(pg_temp.send(121)->>'id' = '00000000-0000-0000-0000-000000000121', 'an hour later it takes feedback again');
reset role;
select pg_temp.assert_true((select count(*) = 21 from net.test_requests
  where body->>'user_id' = '00000000-0000-0000-0000-000000000091'), 'and delivers it');
select 'PASS: feedback hourly limit, retries past it and unchanged delivery for accepted rows';
