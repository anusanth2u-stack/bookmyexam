# Bookmyexam.in

A competitive exam-prep platform (UPSC / PSC / SSC / Banking / Railway) — daily
quizzes, weekly & monthly mega tests, cohort-based ranking, concept videos with
plan-based access, and an admin configurator.

**Stack:** FastAPI (Python) · Supabase (Postgres + Auth + Storage) · Render · GitHub · Razorpay

> This repo is a **runnable MVP foundation**, not the finished product. It boots
> the core loop end to end — auth → daily quiz → tests → async ranking → concepts
> → banners → admin controls (including the rank/banner visibility settings).
> The single-page UI in `frontend/index.html` still runs on mock data; wiring each
> screen to its API endpoint (see `frontend/api.js`) is the next milestone.

---

## What's in here

```
bookmyexam/
├── backend/            FastAPI app
│   ├── main.py         entrypoint (routers, CORS, serves the frontend)
│   ├── config.py       env settings
│   ├── deps.py         Supabase JWT verification, admin guard
│   ├── supabase_client.py
│   └── routers/        student.py, admin.py
├── database/           run these in the Supabase SQL editor, in order
│   ├── 01_schema.sql
│   ├── 02_functions.sql
│   ├── 03_rls.sql
│   └── 04_seed.sql
├── frontend/           index.html (UI) + api.js (client to wire it up)
├── scripts/            compute_ranks.py (scheduled ranking job)
├── render.yaml         Render Blueprint (web service + cron)
├── requirements.txt
├── .env.example
└── .github/workflows/ci.yml
```

---

## 1 — Supabase

1. Create a project at <https://supabase.com>.
2. **SQL Editor → New query** and run the four files **in order**:
   `01_schema.sql`, `02_functions.sql`, `03_rls.sql`, `04_seed.sql`.
3. **Authentication → Providers → Google:** enable it, paste your Google OAuth
   client ID/secret, and add your site + Render URLs to the redirect allow-list.
   (Email/password works out of the box.)
4. **Storage:** create a public bucket named `banners` for banner images. Upload
   images there and use their public URLs in the admin banner form.
5. **Project Settings → API:** copy these into your env vars —
   - `SUPABASE_URL` (Project URL)
   - `SUPABASE_ANON_KEY` (anon / public key — safe for the browser)
   - `SUPABASE_SERVICE_KEY` (service_role key — **server only**)
   - `SUPABASE_JWT_SECRET` (JWT Settings → JWT secret)
6. After you sign up once, make yourself an admin:
   ```sql
   update profiles set role='admin' where email='you@example.com';
   ```

---

## 2 — Run locally

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your Supabase values
uvicorn backend.main:app --reload
```

Open <http://localhost:8000> for the UI and <http://localhost:8000/docs> for the
interactive API. `GET /api/health` should return `{"status":"ok"}`.

---

## 3 — GitHub

```bash
git init && git add . && git commit -m "Bookmyexam MVP scaffold"
git branch -M main
git remote add origin https://github.com/<you>/bookmyexam.git
git push -u origin main
```

CI (`.github/workflows/ci.yml`) installs deps and checks the app imports on every push.

---

## 4 — Render

1. **New + → Blueprint**, point it at your repo. Render reads `render.yaml` and
   creates two services: the **web API** and the **ranking cron**.
2. In each service's **Environment**, set the secret vars from step 1
   (`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY`,
   `SUPABASE_JWT_SECRET`, and Razorpay keys when ready).
3. Deploy. The web service serves both the API and the UI; the cron runs
   `compute_ranks.py` every 15 minutes.
4. Add your Render URL to Supabase Auth's redirect allow-list (step 1.3).

---

## How the core pieces work

- **Auth:** Supabase Auth issues a JWT; the backend verifies it with the JWT
  secret (`deps.py`). A DB trigger auto-creates a `profiles` row on signup.
- **Ranking is asynchronous.** Live tests never compute ranks on the request
  path. After a test's `ends_at` passes, the cron calls `compute_test_ranks()`
  which writes percentile + national/state/district ranks. You're ranked only
  on tests you actually attempt; the **free daily quiz** gives free users a
  daily rank, while **All-India rank** comes from the paid mega tests.
- **Plan-based access:** concept videos carry an `access[]` of plan tiers;
  the API hands over the video/notes only if your plan is allowed.
- **Visibility settings:** `app_settings` holds `rank_visibility` and
  `banner_visibility` as per-plan flags. `/api/me` returns `rank_visible` /
  `banner_visible` so the UI shows ranks, banners, or neither.
- **Security:** the backend uses the service-role key and enforces checks in
  code; RLS policies (`03_rls.sql`) are defense-in-depth for any direct client
  access. Quiz answers/explanations are never sent to a student before submit.

---

## Still to build (post-MVP)

Wire `index.html` to `api.js` · Razorpay orders + webhooks + the unlock flow ·
PDF rank reports · certificates · referrals rewards · notifications/email ·
Excel import endpoint for bulk questions · per-window plan access precision.

---

## API quick reference

| Method | Path | Who |
|---|---|---|
| GET | `/api/me` | student |
| GET | `/api/daily-quiz` | student |
| POST | `/api/attempts` | student |
| GET | `/api/tests` | student |
| GET | `/api/concepts` | student |
| GET | `/api/banners` | student |
| GET | `/api/leaderboard?scope=daily` | student |
| GET | `/api/current-affairs` | student |
| PUT | `/api/admin/settings` | admin |
| POST/DELETE | `/api/admin/banners` | admin |
| POST/DELETE | `/api/admin/concepts` | admin |
| POST | `/api/admin/affairs` | admin |
| POST | `/api/admin/tests` | admin |

Not legal or financial advice; Razorpay/GST invoicing must follow Indian
compliance rules before you take real payments.
