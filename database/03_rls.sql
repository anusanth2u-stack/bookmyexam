-- ============================================================
--  Bookmyexam.in — Row Level Security
--  The FastAPI backend uses the SERVICE ROLE key (bypasses RLS) and
--  enforces access in code. These policies are defense-in-depth for any
--  direct Supabase client access (e.g. the JS SDK in the browser).
--
--  NOTE: RLS cannot hide individual columns. Never expose question_options
--  (is_correct) or questions.explanation to a student before they submit —
--  serve quiz questions through the API, which strips the answers.
-- ============================================================

alter table profiles          enable row level security;
alter table subscriptions     enable row level security;
alter table payments          enable row level security;
alter table questions         enable row level security;
alter table question_options  enable row level security;
alter table tests             enable row level security;
alter table test_questions    enable row level security;
alter table user_test_access  enable row level security;
alter table attempts          enable row level security;
alter table attempt_answers   enable row level security;
alter table results           enable row level security;
alter table rankings          enable row level security;
alter table current_affairs   enable row level security;
alter table concepts          enable row level security;
alter table banners           enable row level security;
alter table app_settings      enable row level security;
alter table referrals         enable row level security;

-- profiles: read/update your own row; admins see all
create policy profiles_self_read   on profiles for select using (id = auth.uid() or is_admin());
create policy profiles_self_update on profiles for update using (id = auth.uid()) with check (id = auth.uid());
create policy profiles_admin_all   on profiles for all    using (is_admin()) with check (is_admin());

-- subscriptions / payments: read your own; writes happen via backend (service role)
create policy subs_self    on subscriptions for select using (user_id = auth.uid() or is_admin());
create policy pay_self     on payments      for select using (user_id = auth.uid() or is_admin());
create policy access_self  on user_test_access for select using (user_id = auth.uid() or is_admin());

-- questions / tests catalogue: published content readable by signed-in users; admins manage
create policy q_read    on questions       for select using (is_active or is_admin());
create policy q_admin   on questions       for all    using (is_admin()) with check (is_admin());
create policy opt_read  on question_options for select using (true);          -- API must strip is_correct pre-submit
create policy opt_admin on question_options for all   using (is_admin()) with check (is_admin());
create policy t_read    on tests           for select using (is_published or is_admin());
create policy t_admin   on tests           for all    using (is_admin()) with check (is_admin());
create policy tq_read   on test_questions  for select using (true);
create policy tq_admin  on test_questions  for all    using (is_admin()) with check (is_admin());

-- attempts / answers / results / rankings: your own
create policy att_self    on attempts        for all    using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy ans_self    on attempt_answers for all
  using (exists (select 1 from attempts a where a.id = attempt_id and a.user_id = auth.uid()))
  with check (exists (select 1 from attempts a where a.id = attempt_id and a.user_id = auth.uid()));
create policy res_self    on results   for select using (user_id = auth.uid() or is_admin());
create policy rank_read   on rankings  for select using (user_id = auth.uid() or is_admin());

-- current affairs: everyone signed-in reads published; admins manage
create policy ca_read  on current_affairs for select using (is_published or is_admin());
create policy ca_admin on current_affairs for all    using (is_admin()) with check (is_admin());

-- concepts: viewable if free, or your plan is in the access[] array; admins manage
create policy concept_read on concepts for select
  using (is_active and ('free' = any(access) or current_plan() = any(access)) or is_admin());
create policy concept_admin on concepts for all using (is_admin()) with check (is_admin());

-- banners: signed-in users read active; admins manage. (API still filters by banner_visibility.)
create policy banner_read  on banners for select using (is_active or is_admin());
create policy banner_admin on banners for all    using (is_admin()) with check (is_admin());

-- settings: everyone reads the single row; only admins write
create policy settings_read  on app_settings for select using (true);
create policy settings_admin on app_settings for all    using (is_admin()) with check (is_admin());

-- referrals
create policy ref_self on referrals for select using (referrer_id = auth.uid() or is_admin());
