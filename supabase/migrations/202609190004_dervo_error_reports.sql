-- Error reports (PLAN-release-signing-and-error-reports.md, part 2): the shape of an error, never its text.
-- The app's allowlist (backend/error_reports.py) is the contract; the checks below are the server's own limits on top of
-- it. Every text column has a fixed shape that can't hold a sentence, a path or an email: an exception class name, a
-- Dervo source file name, a function name, an operation or screen name, a provider word and the app version.
-- Rows follow the usage events' rules (202609190002_dervo_usage_events.sql): a signed-in user adds rows only as
-- themselves and never reads them. Admins read grouped counts through dervo_admin_errors().
begin;

create table public.dervo_error_reports (
  id bigint generated always as identity primary key,
  user_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
  install_id uuid not null,
  source text not null check (source in ('host', 'renderer', 'task')),
  error_type text not null check (error_type ~ '^[A-Za-z_][A-Za-z0-9_]{0,59}$'),
  -- Where in Dervo's own code: a file name without a folder, a function (Class.method, <lambda>) and a line.
  file text check (file ~ '^[a-z_][a-z0-9_]{0,59}\.py$'),
  function text check (function ~ '^[A-Za-z_<][A-Za-z0-9_.<>]{0,79}$'),
  line integer check (line between 1 and 100000),
  -- The host operation (coding_permission) or renderer screen (home, settings.model) that was running.
  operation text check (operation ~ '^[a-z_][a-z0-9_.]{0,39}$'),
  provider text check (provider in ('claude', 'codex')),
  app_version text check (app_version ~ '^[A-Za-z0-9.+_-]{1,40}$'),
  -- The same error repeated between two sends is one row with a count, so a crash loop can't flood the table.
  count integer not null default 1 check (count between 1 and 10000),
  created_at timestamptz not null default now(),
  check ((file is null) = (function is null) and (file is null) = (line is null))
);
create index dervo_error_reports_time on public.dervo_error_reports (created_at);

alter table public.dervo_error_reports enable row level security;
revoke all on public.dervo_error_reports from public, anon, authenticated;
grant insert (install_id, source, error_type, file, function, line, operation, provider, app_version, count)
  on public.dervo_error_reports to authenticated;
create policy dervo_error_insert on public.dervo_error_reports for insert to authenticated
  with check (user_id = (select auth.uid()));
-- No select, update or delete policy and no grant for them: a tester can't read back their own rows or anyone else's.

-- The Testers page's Errors section: one row per kind of error and place, newest first. Counts only, never a report.
create function public.dervo_admin_errors(days integer default 7)
returns jsonb
language plpgsql stable security definer set search_path = '' as $$
declare
  span integer := least(greatest(coalesce(dervo_admin_errors.days, 7), 1), 90);
  since timestamptz := date_trunc('day', now()) - make_interval(days => span - 1);
begin
  if not exists (select 1 from dervo_private.admins a where a.user_id = (select auth.uid())) then
    raise exception 'Dervo admin access required' using errcode = '42501';
  end if;
  return jsonb_build_object(
    'since', since,
    'days', span,
    'errors', coalesce((select jsonb_agg(jsonb_build_object('source', g.source, 'error_type', g.error_type, 'file', g.file,
        'function', g.function, 'line', g.line, 'operation', g.operation, 'provider', g.provider, 'count', g.n,
        'users', g.users, 'first_seen', g.first_seen, 'last_seen', g.last_seen, 'app_version', g.app_version)
        order by g.last_seen desc)
      from (select e.source, e.error_type, e.file, e.function, e.line, e.operation, e.provider, sum(e.count) as n,
                   count(distinct e.user_id) as users, min(e.created_at) as first_seen, max(e.created_at) as last_seen,
                   (array_agg(e.app_version order by e.created_at desc))[1] as app_version
            from public.dervo_error_reports e where e.created_at >= since
            group by 1, 2, 3, 4, 5, 6, 7
            order by max(e.created_at) desc limit 200) g), '[]'::jsonb)
  );
end;
$$;
revoke all on function public.dervo_admin_errors(integer) from public, anon, authenticated;
grant execute on function public.dervo_admin_errors(integer) to authenticated;

commit;
