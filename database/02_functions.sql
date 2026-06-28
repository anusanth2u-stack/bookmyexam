-- ============================================================
--  Bookmyexam.in — Functions & triggers
-- ============================================================

-- keep updated_at fresh
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

do $$
declare t text;
begin
  foreach t in array array['profiles','subscriptions','payments','questions','tests'] loop
    execute format('drop trigger if exists trg_updated_%1$s on %1$s;', t);
    execute format('create trigger trg_updated_%1$s before update on %1$s
                    for each row execute function set_updated_at();', t);
  end loop;
end $$;

-- auto-create a profile row when a new auth user signs up.
-- Supabase Auth puts Google/sign-up data in raw_user_meta_data.
create or replace function handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, full_name, role, plan)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'full_name', new.raw_user_meta_data->>'name', ''),
    'student',
    'free'
  )
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- is the caller an admin? (SECURITY DEFINER avoids RLS recursion)
create or replace function is_admin() returns boolean
language sql security definer stable set search_path = public as $$
  select exists (select 1 from profiles where id = auth.uid() and role = 'admin');
$$;

-- the caller's current plan
create or replace function current_plan() returns plan_tier
language sql security definer stable set search_path = public as $$
  select coalesce(
    (select plan from profiles where id = auth.uid()
      and (plan_expires_at is null or plan_expires_at > now())),
    'free'::plan_tier);
$$;

-- ------------------------------------------------------------
-- compute_test_ranks(test_id): percentile + national/state/district
-- ranks for everyone who attempted a test. Run AFTER the test window
-- closes (scheduled job), never live during the test.
-- ------------------------------------------------------------
create or replace function compute_test_ranks(p_test_id uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  update tests set ranking_status = 'computing' where id = p_test_id;

  with ranked as (
    select r.id as result_id, r.user_id, r.score, pr.state, pr.district,
           percent_rank() over (order by r.score)                                  as pr_val,
           row_number()  over (order by r.score desc)                              as nat,
           row_number()  over (partition by pr.state    order by r.score desc)     as st,
           row_number()  over (partition by pr.district order by r.score desc)     as dist
    from results r
    join profiles pr on pr.id = r.user_id
    where r.test_id = p_test_id
  )
  insert into rankings(result_id,user_id,test_id,percentile,national_rank,state_rank,district_rank,state,district,computed_at)
  select result_id, user_id, p_test_id, round((pr_val*100)::numeric,2), nat, st, dist, state, district, now()
  from ranked
  on conflict (result_id) do update
     set percentile    = excluded.percentile,
         national_rank = excluded.national_rank,
         state_rank    = excluded.state_rank,
         district_rank = excluded.district_rank,
         computed_at   = now();

  update tests set ranking_status = 'done' where id = p_test_id;
end $$;
