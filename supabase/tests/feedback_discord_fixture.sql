-- Local stand-ins only. No HTTP requests or real secrets are used by these tests.
alter table auth.users add column email_confirmed_at timestamptz default now();
alter table auth.users add column is_anonymous boolean default false;
create schema vault;
create table vault.decrypted_secrets(name text unique, decrypted_secret text);
create schema net;
create table net.test_requests(id bigint generated always as identity, url text, body jsonb, headers jsonb);
create function net.http_post(url text, body jsonb default '{}', params jsonb default '{}',
  headers jsonb default '{}', timeout_milliseconds integer default 2000) returns bigint
language plpgsql as $$
declare request_id bigint;
begin
  if current_setting('test.net_fails',true) = 'yes' then raise exception 'synthetic transport failure'; end if;
  insert into net.test_requests(url,body,headers) values(url,body,headers) returning id into request_id;
  return request_id;
end;
$$;
