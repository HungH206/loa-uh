# LOA without an always-on Pi

The Vercel frontend can start directly from public Supabase configuration. Login, incident report submission/history, and account preferences talk to Supabase directly, protected by row-level security. The optional Pi remains the MQTT receiver and store for ESP32 measurements and hub alerts. Tailscale is needed only to reach that private hub, not for cloud features.

## Configuration

Apply both SQL migrations under `supabase/migrations` in Supabase SQL Editor. Configure Production and Preview environment variables in Vercel:

- `VITE_SUPABASE_URL`: the existing Supabase project URL.
- `VITE_SUPABASE_PUBLISHABLE_KEY`: the public publishable/anon key, never a secret/service-role key.
- `VITE_BACKEND_URL`: optional Pi HTTPS origin. Leave empty to run without any hub.

Redeploy after changing these build-time values. A private `.local/vercel-public.env` was generated from this checkout's existing public configuration for setup; do not copy the backend `.env` wholesale. Local frontend development can use the same public values in ignored `frontend/.env.local`.

If cloud configuration is absent, the frontend retains legacy same-origin/backend configuration loading with an eight-second timeout. Production should have both Supabase variables so it never depends on `/api/config` at the Pi.

## Behavior

- **Reports:** reads/writes go directly to `incident_reports`, preserving the existing schema and request-ID deduplication. Internet access to Supabase is required. A cloud error retains the form and does not claim a successful save.
- **Preferences:** stored per account in `user_preferences`. New accounts default to -40 dBFS, three seconds, and a 30-second cooldown. Existing hub preferences are not imported automatically. Saving preferences applies them to browser monitoring; use **Apply saved preferences to connected hub** to explicitly copy them to that Pi. A disconnected hub keeps its last settings.
- **Browser microphone:** operates locally without a Pi, after permission. Numeric readings and exceedances stay in memory for this page session, capped at 10,000 readings/50 events. Refreshing or closing the page clears them; they are not cloud history. Raw audio is not uploaded. Stop or hiding the page releases the microphone.
- **ESP32:** pairing, live measurements, stored history, and hub-generated alerts require a reachable Pi. Last-loaded history may remain visible in the open page, but fresh hub history cannot load while offline. MQTT traffic is not moved to Vercel.
- **Hub outage:** the page shows **Hub offline · cloud available**. Optional hub requests time out after five seconds and back off for 30 seconds; cloud requests do not wait for them. Connecting/reconnecting the hub restores live updates and device data. Browser monitoring remains available.

## Checks performed

- Frontend unit tests: `npm test --prefix frontend` verifies cloud request ownership, retry-safe reporting, error handling, preferences, and local browser alert duration/gaps.
- Isolated PostgreSQL tests: `.venv/bin/python scripts/verify_report_rls.py` checks both migrations and account isolation with emulated Supabase auth roles.
- Automated Chrome check with fake Supabase responses and all Pi requests blocked: startup/login, report submission, microphone start/stop, and preference saving passed with no page errors. This checks UI behavior; it is not a live authenticated Supabase submission.
- Backend API regression tests: `.venv/bin/python -m pytest -q`.

For the real demonstration, power off the Pi, open Vercel without Tailscale, sign in, submit a report, verify it in Supabase Table Editor, save preferences, and try local microphone monitoring. Power the Pi back on and connect Tailscale to demonstrate the separate hardware path recovering.
