# Incident reports in Supabase Postgres

LOA keeps sensor data, alerts, pairing, and preferences on the Raspberry Pi. When `LOA_REPORT_STORE=supabase`, incident reports use the Supabase `public.incident_reports` table. Supabase stores proper timestamp columns so incidents are readable in its developer tools.

## Setup

1. In the same Supabase project used for LOA login, run `supabase/migrations/202611150001_incident_reports.sql` in SQL Editor.
2. Set `LOA_REPORT_STORE=supabase` in `/etc/loa/loa.env` on the Pi.
3. Restart `loa.service`, refresh the dashboard, and open Report disturbance.

The Pi calls the Supabase REST API using the existing publishable key and the signed-in user's access token. It does not need a database password, service-role key, or admin token. Row-level security restricts SELECT and INSERT to rows whose `user_id` matches `auth.uid()`. Anonymous access, application updates, and application deletes are not granted. Project administrators can inspect reports through Supabase's management tools.

## Developer view

Open **Supabase Dashboard → Table Editor → incident_reports**. You can also run this in SQL Editor:

```sql
select id, user_id, occurred_at, created_at, location, description, feeling, details
from public.incident_reports
order by occurred_at desc, id desc
limit 50;
```

Create a report in LOA, then refresh Table Editor to see it. Times are stored as timestamptz and displayed by LOA in the viewer's local time. The dashboard shows only the latest 50 incidents for the signed-in account; Table Editor is the administrative view of the underlying records.

## Existing Pi reports

Opening Report disturbance uploads that signed-in account's old SQLite reports in batches. Local originals are preserved. The Pi records successful migration in `report_cloud_sync`, scoped to Supabase project, account, and request ID. Other accounts' reports migrate only when those users sign in and open the reports screen. A unique `(user_id, request_id)` constraint makes migration and submission retries idempotent. If a cloud insert succeeds but its acknowledgement is lost, retrying retrieves the existing report.

New reports in cloud mode are stored in Supabase, not duplicated into the legacy SQLite table. On a cloud error, the form keeps its entered values and shows an error; it does not claim a local-only report was saved to the cloud. This requires internet connectivity. Switching back to `sqlite` exposes only the legacy local records; it does not download newer cloud reports. Do not use that switch as a complete cloud-data rollback.

## Verification

- `.venv/bin/python -m pytest -q`: local APIs and mocked cloud API checks for token forwarding, owner-scoped queries, retries, migration, and cloud failures.
- `.venv/bin/python scripts/verify_report_rls.py`: real PostgreSQL schema/policy checks in an isolated temporary local cluster, with minimal emulated Supabase roles/auth.uid(). Requires PostgreSQL binaries, such as Postgres.app.
- Cloud verification: anonymous reads must be denied; then submit while signed in and verify the row in Table Editor. Test a second account to confirm no report is exposed to it.

The local PostgreSQL checks validate SQL/RLS behavior, not the live Supabase project's authentication, grants, or configuration. Never expose admin credentials in the frontend or share them through chat.
