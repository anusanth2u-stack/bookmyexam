-- ============================================================
--  Bookmyexam.in — Database schema (Supabase / PostgreSQL)
--  Run order: 01_schema.sql -> 02_functions.sql -> 03_rls.sql -> 04_seed.sql
-- ============================================================

create extension if not exists pgcrypto;

-- ---------- enums ----------
do $$ begin
  create type user_role     as enum ('student','admin');
  create type plan_tier      as enum ('free','weekly','monthly','quarterly','yearly');
  create type test_type      as enum ('daily','weekly','monthly','quarterly','annual');
  create type attempt_status as enum ('in_progress','submitted','auto_submitted','expired');
  create type rank_status    as enum ('pending','computing','done');
  create type pay_status     as enum ('created','paid','failed','refunded');
exception when duplicate_object then null; end $$;

-- ---------- profiles (extends auth.users) ----------
create table if not exists profiles (
  id              uuid primary key references auth.users(id) on delete cascade,
  full_name       text,
  mobile          text,
  email           text,
  state           text,
  district        text,
  target_exam     text,
  exam_year       int,
  role            user_role  not null default 'student',
  plan            plan_tier  not null default 'free',
  plan_expires_at timestamptz,
  referral_code   text unique default substr(md5(gen_random_uuid()::text),1,8),
  referred_by     uuid references profiles(id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- ---------- subscriptions & payments ----------
create table if not exists subscriptions (
  id                      uuid primary key default gen_random_uuid(),
  user_id                 uuid not null references profiles(id) on delete cascade,
  plan                    plan_tier not null,
  status                  text not null default 'active',          -- active | expired | cancelled | pending
  razorpay_subscription_id text,
  starts_at               timestamptz not null default now(),
  ends_at                 timestamptz,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now()
);
create index if not exists idx_subscriptions_user on subscriptions(user_id);

create table if not exists payments (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references profiles(id) on delete cascade,
  subscription_id    uuid references subscriptions(id),
  test_id            uuid,                                          -- set when unlocking a single past test
  razorpay_order_id  text,
  razorpay_payment_id text,
  amount_paise       int  not null,                                -- store money as integer paise
  currency           text not null default 'INR',
  status             pay_status not null default 'created',
  method             text,
  invoice_number     text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);
create index if not exists idx_payments_user on payments(user_id);

-- ---------- question bank ----------
create table if not exists questions (
  id             uuid primary key default gen_random_uuid(),
  subject        text,
  topic          text,
  difficulty     text default 'medium',                            -- easy | medium | hard
  question_text  text not null,
  explanation    text,
  video_url      text,                                             -- concept video for the solution (premium tests)
  marks          numeric not null default 1,
  negative_marks numeric not null default 0,
  language       text not null default 'en',
  created_by     uuid references profiles(id),
  is_active      boolean not null default true,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);
create index if not exists idx_questions_subject on questions(subject);

create table if not exists question_options (
  id           uuid primary key default gen_random_uuid(),
  question_id  uuid not null references questions(id) on delete cascade,
  option_text  text not null,
  is_correct   boolean not null default false,
  option_order int not null default 0
);
create index if not exists idx_options_question on question_options(question_id);

-- ---------- tests ----------
create table if not exists tests (
  id              uuid primary key default gen_random_uuid(),
  title           text not null,
  test_type       test_type not null,
  description     text,
  duration_minutes int not null default 30,
  total_questions int,
  total_marks     numeric,
  negative_marking boolean not null default true,
  is_free         boolean not null default false,
  price_paise     int not null default 0,
  month           text,                                            -- 'YYYY-MM' for month-wise grouping
  go_live_at      timestamptz,
  ends_at         timestamptz,                                     -- ranking is computed after this
  ranking_status  rank_status not null default 'pending',
  is_published    boolean not null default false,
  created_by      uuid references profiles(id),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists idx_tests_month on tests(month);
create index if not exists idx_tests_type  on tests(test_type);

create table if not exists test_questions (
  id            uuid primary key default gen_random_uuid(),
  test_id       uuid not null references tests(id) on delete cascade,
  question_id   uuid not null references questions(id) on delete cascade,
  question_order int not null default 0,
  marks         numeric,
  negative_marks numeric,
  unique(test_id, question_id)
);
create index if not exists idx_tq_test on test_questions(test_id);

-- individually unlocked past tests (the "unlock ₹49" flow)
create table if not exists user_test_access (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references profiles(id) on delete cascade,
  test_id     uuid not null references tests(id) on delete cascade,
  payment_id  uuid references payments(id),
  unlocked_at timestamptz not null default now(),
  unique(user_id, test_id)
);

-- ---------- attempts / answers / results / rankings ----------
create table if not exists attempts (
  id           uuid primary key default gen_random_uuid(),
  test_id      uuid not null references tests(id) on delete cascade,
  user_id      uuid not null references profiles(id) on delete cascade,
  status       attempt_status not null default 'in_progress',
  started_at   timestamptz not null default now(),
  submitted_at timestamptz,
  unique(test_id, user_id)              -- one ranked attempt per test
);
create index if not exists idx_attempts_test on attempts(test_id);

create table if not exists attempt_answers (
  id                 uuid primary key default gen_random_uuid(),
  attempt_id         uuid not null references attempts(id) on delete cascade,
  question_id        uuid not null references questions(id) on delete cascade,
  selected_option_id uuid references question_options(id),         -- null = unanswered
  is_correct         boolean,
  marks_awarded      numeric,
  answered_at        timestamptz not null default now(),
  unique(attempt_id, question_id)
);

create table if not exists results (
  id                uuid primary key default gen_random_uuid(),
  attempt_id        uuid not null unique references attempts(id) on delete cascade,
  user_id           uuid not null references profiles(id) on delete cascade,
  test_id           uuid not null references tests(id) on delete cascade,
  score             numeric not null default 0,
  total_marks       numeric,
  correct_count     int not null default 0,
  incorrect_count   int not null default 0,
  unattempted_count int not null default 0,
  accuracy          numeric,
  time_taken_seconds int,
  created_at        timestamptz not null default now()
);
create index if not exists idx_results_test on results(test_id);
create index if not exists idx_results_user on results(user_id);

create table if not exists rankings (
  id            uuid primary key default gen_random_uuid(),
  result_id     uuid not null unique references results(id) on delete cascade,
  user_id       uuid not null references profiles(id) on delete cascade,
  test_id       uuid not null references tests(id) on delete cascade,
  percentile    numeric,
  national_rank int,
  state_rank    int,
  district_rank int,
  state         text,
  district      text,
  computed_at   timestamptz not null default now()
);
create index if not exists idx_rankings_test_nat on rankings(test_id, national_rank);

-- ---------- content: current affairs & concepts ----------
create table if not exists current_affairs (
  id           uuid primary key default gen_random_uuid(),
  category     text not null,
  title        text not null,
  body         text not null,
  image_url    text,
  published_at date not null default current_date,
  is_published boolean not null default true,
  created_by   uuid references profiles(id),
  created_at   timestamptz not null default now()
);
create index if not exists idx_affairs_date on current_affairs(published_at desc);

create table if not exists concepts (
  id         uuid primary key default gen_random_uuid(),
  title      text not null,
  subject    text,
  video_url  text not null,             -- YouTube watch/share URL or 11-char id
  notes      text,
  access     plan_tier[] not null default '{free}',   -- which plans can view
  is_active  boolean not null default true,
  created_by uuid references profiles(id),
  created_at timestamptz not null default now()
);

-- ---------- promo banners ----------
create table if not exists banners (
  id         uuid primary key default gen_random_uuid(),
  image_url  text not null,
  link_url   text,
  title      text,
  sort_order int not null default 0,
  is_active  boolean not null default true,
  created_at timestamptz not null default now()
);

-- ---------- singleton app settings (rank + banner visibility) ----------
create table if not exists app_settings (
  id                int primary key default 1,
  rank_visibility   jsonb not null default '{"guest":true,"weekly":true,"monthly":true,"quarterly":true,"yearly":true}',
  banner_visibility jsonb not null default '{"guest":true,"weekly":true,"monthly":true,"quarterly":true,"yearly":true}',
  updated_at        timestamptz not null default now(),
  constraint settings_singleton check (id = 1)
);

-- ---------- referrals ----------
create table if not exists referrals (
  id          uuid primary key default gen_random_uuid(),
  referrer_id uuid not null references profiles(id) on delete cascade,
  referred_id uuid references profiles(id),
  code        text not null,
  reward      text,
  created_at  timestamptz not null default now()
);

-- ---------- audit log (phase 2) ----------
create table if not exists audit_logs (
  id         uuid primary key default gen_random_uuid(),
  actor_id   uuid references profiles(id),
  action     text,
  entity     text,
  entity_id  uuid,
  meta       jsonb,
  created_at timestamptz not null default now()
);
