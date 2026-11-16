"""Verify the report SQL and RLS in an isolated local PostgreSQL cluster.

Requires PostgreSQL tools in PATH (or Postgres.app on macOS). No cloud credentials.
"""
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = Path('/Applications/Postgres.app/Contents/Versions/latest/bin')


def tool(name):
    return shutil.which(name) or str(BIN / name)


def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


with tempfile.TemporaryDirectory(prefix='loa-rls-') as directory:
    folder = Path(directory)
    data = folder / 'data'
    run([tool('initdb'), '-D', str(data), '-A', 'trust', '--no-locale'])
    run([tool('pg_ctl'), '-D', str(data), '-l', str(folder/'server.log'),
         '-o', f"-k {folder} -h '' -p 55439", '-w', 'start'])
    try:
        def sql(statement):
            return run([tool('psql'), '-h', str(folder), '-p', '55439', '-d', 'postgres',
                        '-v', 'ON_ERROR_STOP=1', '-X'], input=statement)
        sql("""
        create role anon; create role authenticated;
        create schema auth;
        create table auth.users(id uuid primary key);
        create function auth.uid() returns uuid language sql stable as
          $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
        grant usage on schema auth to anon, authenticated;
        grant execute on function auth.uid() to anon, authenticated;
        insert into auth.users values ('11111111-1111-4111-8111-111111111111'),
                                      ('22222222-2222-4222-8222-222222222222');
        """)
        migration = (ROOT/'supabase/migrations/202611150001_incident_reports.sql').read_text()
        sql(migration)
        sql(migration)  # Reapplying the migration must be safe.
        sql("""
        set role authenticated;
        set request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';
        insert into public.incident_reports(user_id,request_id,occurred_at,location,description,feeling)
          values(auth.uid(),'33333333-3333-4333-8333-333333333333',now(),'Library','Music','Tolerable');
        insert into public.incident_reports(user_id,request_id,occurred_at,location,description,feeling)
          values(auth.uid(),'33333333-3333-4333-8333-333333333333',now(),'Library','Music','Tolerable')
          on conflict(user_id,request_id) do nothing;
        do $$ begin
          if (select count(*) from public.incident_reports) <> 1 then raise exception 'retry duplicated'; end if;
          begin
            insert into public.incident_reports(user_id,request_id,occurred_at,location,description,feeling)
              values('22222222-2222-4222-8222-222222222222',gen_random_uuid(),now(),'Home','Music','Sensitive');
            raise exception 'cross-account insert was allowed';
          exception when insufficient_privilege then null; end;
          begin
            update public.incident_reports set location='Other';
            raise exception 'update was allowed';
          exception when insufficient_privilege then null; end;
          begin
            delete from public.incident_reports;
            raise exception 'delete was allowed';
          exception when insufficient_privilege then null; end;
        end $$;
        set request.jwt.claim.sub = '22222222-2222-4222-8222-222222222222';
        do $$ begin
          if (select count(*) from public.incident_reports) <> 0 then raise exception 'private rows leaked'; end if;
        end $$;
        insert into public.incident_reports(user_id,request_id,occurred_at,location,description,feeling)
          values(auth.uid(),'33333333-3333-4333-8333-333333333333',now(),'Home','Traffic','Need to Cope');
        set role anon;
        do $$ begin
          begin
            perform * from public.incident_reports;
            raise exception 'anonymous access was allowed';
          exception when insufficient_privilege then null; end;
        end $$;
        reset role;
        do $$ begin
          if (select count(*) from public.incident_reports) <> 2 then raise exception 'unexpected row count'; end if;
        end $$;
        """)
        sql((ROOT/'supabase/migrations/202611150002_user_preferences.sql').read_text())
        sql("""
        set role authenticated;
        set request.jwt.claim.sub = '11111111-1111-4111-8111-111111111111';
        insert into public.user_preferences(user_id) values(auth.uid());
        update public.user_preferences set threshold=-30 where user_id=auth.uid();
        set request.jwt.claim.sub = '22222222-2222-4222-8222-222222222222';
        do $$ begin
          if (select count(*) from public.user_preferences) <> 0 then raise exception 'preferences leaked'; end if;
          begin
            insert into public.user_preferences(user_id) values('11111111-1111-4111-8111-111111111111');
            raise exception 'foreign preferences insert allowed';
          exception when insufficient_privilege then null; end;
        end $$;
        insert into public.user_preferences(user_id) values(auth.uid());
        update public.user_preferences set threshold=-25 where user_id='11111111-1111-4111-8111-111111111111';
        reset role;
        do $$ begin
          if (select threshold from public.user_preferences where user_id='11111111-1111-4111-8111-111111111111') <> -30 then
            raise exception 'foreign preferences update allowed'; end if;
        end $$;
        """)
        print('PostgreSQL checks passed: schema, rerun, own reads/inserts, cross-account isolation, retry deduplication, denied anonymous/update/delete access.')
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr)
    finally:
        run([tool('pg_ctl'), '-D', str(data), '-m', 'fast', '-w', 'stop'])
