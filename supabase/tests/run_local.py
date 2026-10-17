"""Run account policy checks on a disposable local PostgreSQL cluster.

No hosted connection, Docker image, credentials or persistent database is used.
The small auth/storage fixtures aren't proof of Supabase HTTP or email behaviour.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BOOTSTRAP = """
create role anon nologin;
create role authenticated nologin;
create schema auth;
create table auth.users(id uuid primary key, raw_user_meta_data jsonb default '{}', email varchar(255),
  created_at timestamptz not null default now());
create function auth.uid() returns uuid language sql stable as
  $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
grant usage on schema auth to anon, authenticated;
grant execute on function auth.uid() to anon, authenticated;
insert into auth.users(id) values ('00000000-0000-0000-0000-000000000004');
create schema storage;
create table storage.buckets(id text primary key, name text not null, public boolean not null,
  file_size_limit bigint, allowed_mime_types text[]);
create table storage.objects(id bigint generated always as identity primary key, bucket_id text, name text);
alter table storage.objects enable row level security;
create function storage.foldername(text) returns text[] language sql immutable as
  $$select (string_to_array($1,'/'))[1:array_length(string_to_array($1,'/'),1)-1]$$;
grant usage on schema storage to anon, authenticated;
grant select,insert,update,delete on storage.objects to anon, authenticated;
grant usage on sequence storage.objects_id_seq to anon, authenticated;
create schema extensions;
create extension pgcrypto schema extensions;
grant usage on schema extensions to anon, authenticated;
"""


def main():
    pg_config = shutil.which('pg_config')
    if not pg_config:
        raise SystemExit('Local PostgreSQL is required. No server was contacted.')
    bindir = Path(subprocess.check_output([pg_config, '--bindir'], text=True).strip())
    # Ignore ambient database credentials, services and connection strings.
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'SUPABASE'))}
    env['LC_ALL'] = 'C'
    with tempfile.TemporaryDirectory(prefix='dervo-pg-') as tmp:
        root = Path(tmp)
        # /tmp keeps PostgreSQL's Unix socket path within its platform length limit.
        with tempfile.TemporaryDirectory(prefix='dervo-pgs-', dir='/tmp') as socket:
            data = root / 'data'
            def run(name, *args, text=None):
                result = subprocess.run([str(bindir/name), *args], input=text, text=True,
                                        env=env, capture_output=True, timeout=60)
                if result.returncode:
                    # These inputs and this cluster contain only fixed test identities.
                    raise RuntimeError(result.stderr or result.stdout)
                return result.stdout
            run('initdb', '-D', str(data), '-A', 'trust', '--no-locale', '--encoding=UTF8')
            (data/'postgresql.conf').write_text("listen_addresses = ''\n" +
                "unix_socket_directories = '" + socket + "'\nmax_connections = 10\nshared_buffers = '16MB'\n")
            started = False
            try:
                run('pg_ctl', '-D', str(data), '-l', str(root/'postgres.log'), '-w', 'start')
                started = True
                args = ('-X', '-h', socket, '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-q')
                run('psql', *args, text=BOOTSTRAP)
                run('psql', *args, '-f', str(HERE/'feedback_discord_fixture.sql'))
                for migration in sorted((HERE.parent/'migrations').glob('*.sql')):
                    run('psql', *args, '-f', str(migration))
                run('psql', *args, '-f', str(HERE/'accounts.sql'))
                run('psql', *args, '-f', str(HERE/'testers.sql'))
                run('psql', *args, '-f', str(HERE/'releases.sql'))
                run('psql', *args, '-f', str(HERE/'feedback_discord.sql'))
                # Feedback's hourly limit (security review L2): twenty a person, retries pass, delivery unchanged.
                run('psql', *args, '-f', str(HERE/'feedback_limit.sql'))
                # Usage events (beta.9): insert-only rows, the admin report.
                run('psql', *args, '-f', str(HERE/'usage_events.sql'))
                # Usage per tester (28 Sep): who's actually doing things, for admins only.
                run('psql', *args, '-f', str(HERE/'usage_people.sql'))
                # Error reports (beta.11): insert-only rows with no free text, the admin's grouped counts.
                run('psql', *args, '-f', str(HERE/'error_reports.sql'))
                # Beta invitations: who may invite, one ID per live invitation, approval on a verified sign-in.
                run('psql', *args, '-f', str(HERE/'invitations.sql'))
                # Founder testers (beta.15, group 7): approved before launch, kept for good, asked about only by themselves.
                run('psql', *args, '-f', str(HERE/'founders.sql'))
                # Tester reports for admins (beta.16, b16/lifecycle): admins read them without emails or ids, testers get nothing.
                run('psql', *args, '-f', str(HERE/'admin_feedback.sql'))
                # Paid plans (8 Oct 2026): the trial, Paddle webhook signatures, and admin choices winning over billing.
                run('psql', *args, '-f', str(HERE/'billing.sql'))
                print('PASS: feedback notification isolation, the hourly feedback limit, and account, admin, membership, testers, avatar and release SQL policy checks, usage events and error reports from approved testers only, release signatures, invitations, founders, tester reports for admins, and the trial and paid plans, on isolated PostgreSQL')
                print('Not checked: hosted migrations, hosted Vault, Paddle delivery, JWT validation, email, Storage HTTP, native sessions')
            finally:
                if started:
                    run('pg_ctl', '-D', str(data), '-m', 'fast', '-w', 'stop')


if __name__ == '__main__':
    main()
