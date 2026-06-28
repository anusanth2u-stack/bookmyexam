-- ============================================================
--  Bookmyexam.in — Seed data (safe to re-run)
-- ============================================================

-- singleton settings row
insert into app_settings (id) values (1) on conflict (id) do nothing;

-- ---- a sample daily quiz for today ----
do $$
declare
  v_test uuid;
  v_q    uuid;
begin
  if not exists (select 1 from tests where test_type='daily' and go_live_at::date = current_date) then
    insert into tests(title,test_type,duration_minutes,total_questions,is_free,is_published,month,go_live_at,ends_at)
    values ('Daily Current Affairs Quiz','daily',3,3,true,true,to_char(current_date,'YYYY-MM'),
            now(), now() + interval '1 day')
    returning id into v_test;

    -- Q1
    insert into questions(subject,topic,question_text,explanation,is_active)
    values ('Polity','Schemes','India''s flagship scheme ''PM Vishwakarma'' primarily supports which group?',
            'PM Vishwakarma gives credit, tools and skill support to traditional artisans such as weavers and potters.',true)
    returning id into v_q;
    insert into question_options(question_id,option_text,is_correct,option_order) values
      (v_q,'Traditional artisans & craftspeople',true,0),
      (v_q,'Startup founders',false,1),
      (v_q,'Smallholder farmers',false,2),
      (v_q,'Urban gig workers',false,3);
    insert into test_questions(test_id,question_id,question_order,marks) values (v_test,v_q,0,1);

    -- Q2
    insert into questions(subject,topic,question_text,explanation,is_active)
    values ('Economy','Institutions','Which body releases the ''Financial Stability Report'' in India?',
            'The RBI publishes the half-yearly Financial Stability Report.',true)
    returning id into v_q;
    insert into question_options(question_id,option_text,is_correct,option_order) values
      (v_q,'SEBI',false,0),(v_q,'Ministry of Finance',false,1),
      (v_q,'Reserve Bank of India',true,2),(v_q,'NITI Aayog',false,3);
    insert into test_questions(test_id,question_id,question_order,marks) values (v_test,v_q,1,1);

    -- Q3
    insert into questions(subject,topic,question_text,explanation,is_active)
    values ('Geography','Physical','The Western Ghats do NOT pass through which state?',
            'The Western Ghats run along the western coast — not Odisha, which lies on the eastern side.',true)
    returning id into v_q;
    insert into question_options(question_id,option_text,is_correct,option_order) values
      (v_q,'Kerala',false,0),(v_q,'Maharashtra',false,1),
      (v_q,'Odisha',true,2),(v_q,'Karnataka',false,3);
    insert into test_questions(test_id,question_id,question_order,marks) values (v_test,v_q,2,1);
  end if;
end $$;

-- ---- sample concepts with access tiers ----
insert into concepts(title,subject,video_url,notes,access) values
  ('Fundamental Rights — explained','Polity','M7lc1UVf-VE',
   'Articles 12-35. Six categories. Article 32 is the heart and soul of the Constitution.', '{free,weekly,monthly,quarterly,yearly}'),
  ('Monetary policy & the RBI','Economy','M7lc1UVf-VE',
   'Repo rate, CRR, SLR. MPC has 6 members and targets 4% (+/-2%) inflation.', '{weekly,monthly,quarterly,yearly}'),
  ('Indian monsoon & climate','Geography','M7lc1UVf-VE',
   'SW monsoon June-Sept brings ~75% of annual rainfall.', '{monthly,quarterly,yearly}')
on conflict do nothing;

-- ---- sample current affairs ----
insert into current_affairs(category,title,body,published_at) values
  ('National','RBI keeps repo rate unchanged at 6.25%','The MPC held rates steady, citing easing inflation and steady growth.', current_date),
  ('Economy','India''s services PMI hits 11-month high','Strong domestic demand pushed the index up.', current_date)
on conflict do nothing;

-- ---- sample banner (replace with a Supabase Storage URL) ----
insert into banners(image_url,link_url,title,sort_order,is_active) values
  ('https://placehold.co/2000x1000/201F39/E0A53A.png?text=Your+UPSC+Banner',
   'https://bookmyexam.in/offers/quarterly','Quarterly plan 30% off',0,true)
on conflict do nothing;

-- ---- to make yourself an admin after signing up, run:
--   update profiles set role='admin' where email='you@example.com';
