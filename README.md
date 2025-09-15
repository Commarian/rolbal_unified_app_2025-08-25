# Rolbal Unified App (Streamlit)

## Run
```
pip install -r requirements.txt
streamlit run app.py
```

## Features
- Players registry, schedule gen (Swiss / Round-robin, no-repeat), rink rotation
- Mirror score entry (B mirrors A), round locks & audit log
- Rules/tiebreakers (win/draw/loss points, optional bonus on big win)
- Standings per section & combined, live leaderboard view
- Import players from Excel (Punte Sek 1/2) and export workbook
- JSON persistence in `./data/event.json`

## Hosted Login (Supabase Auth)

You can enable a simple hosted login (free tier) using Supabase Auth. When configured, users must sign in (email/password or email code), and each signed-in user saves data to a separate file to avoid clashes when multiple users share the same running app instance.

Setup:
- Create a Supabase project (free tier is fine).
- In Authentication settings, enable Email/Password and/or Magic Links.
- In Streamlit, add secrets in `.streamlit/secrets.toml`:

```
[supabase]
url = "https://YOUR-PROJECT.supabase.co"
anon_key = "YOUR_ANON_PUBLIC_KEY"
```

Run as usual. The app will show a Sign In screen. If Supabase is not configured, the app runs in guest mode and uses `data/event.json`.

## Online Storage (no local JSON)

When Supabase Auth is configured, the app stores all state online in your Supabase Postgres DB (per-user row). Create the table and policies in the SQL editor:

```
create table if not exists public.events (
  user_id uuid primary key,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.events enable row level security;

create policy "read own" on public.events
  for select using (auth.uid() = user_id);
create policy "insert own" on public.events
  for insert with check (auth.uid() = user_id);
create policy "update own" on public.events
  for update using (auth.uid() = user_id);
```

No service key is needed; the app uses the anon key with the signed-in user session so RLS restricts access.

This app combines the separate mini-apps into one UI while keeping the data format compatible.
