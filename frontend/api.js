/* Bookmyexam.in — frontend API client.
 *
 * The prototype in index.html currently runs on in-memory mock data so it
 * works offline. To go live, sign in with Supabase Auth, then replace the
 * mock render functions with these calls. Each maps to a backend endpoint.
 *
 * Setup (add to <head> of index.html):
 *   <script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
 */

let supa = null;
let accessToken = null;

// 1) pull public config (Supabase URL + anon key) from the API, init auth
export async function initAuth() {
  const cfg = await fetch("/api/config").then(r => r.json());
  supa = window.supabase.createClient(cfg.supabase_url, cfg.supabase_anon_key);
  const { data } = await supa.auth.getSession();
  accessToken = data.session?.access_token || null;
  supa.auth.onAuthStateChange((_e, session) => { accessToken = session?.access_token || null; });
  return supa;
}

export function signInWithGoogle() {
  return supa.auth.signInWithOAuth({ provider: "google", options: { redirectTo: location.origin } });
}
export const signOut = () => supa.auth.signOut();

// 2) authenticated fetch helper
async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: {
      "Content-Type": "application/json",
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// 3) student endpoints
export const getMe            = ()            => api("/api/me");
export const getDailyQuiz     = ()            => api("/api/daily-quiz");
export const submitAttempt    = (payload)     => api("/api/attempts", { method: "POST", body: JSON.stringify(payload) });
export const getTests         = ()            => api("/api/tests");
export const getConcepts      = ()            => api("/api/concepts");
export const getBanners       = ()            => api("/api/banners");
export const getLeaderboard   = (scope)       => api(`/api/leaderboard?scope=${scope}`);
export const getCurrentAffairs= ()            => api("/api/current-affairs");

// 4) admin endpoints
export const adminUpdateSettings = (body)  => api("/api/admin/settings",  { method: "PUT",    body: JSON.stringify(body) });
export const adminAddBanner      = (body)  => api("/api/admin/banners",   { method: "POST",   body: JSON.stringify(body) });
export const adminDeleteBanner   = (id)    => api(`/api/admin/banners/${id}`, { method: "DELETE" });
export const adminAddConcept     = (body)  => api("/api/admin/concepts",  { method: "POST",   body: JSON.stringify(body) });
export const adminAddAffair      = (body)  => api("/api/admin/affairs",   { method: "POST",   body: JSON.stringify(body) });
export const adminCreateTest     = (body)  => api("/api/admin/tests",     { method: "POST",   body: JSON.stringify(body) });

/* Example — replacing the prototype's mock daily quiz:
 *
 *   const { test, questions } = await getDailyQuiz();
 *   // render questions; on submit:
 *   const result = await submitAttempt({ test_id: test.id, answers });
 *   // result.solutions has explanations (+ video_url for premium tests)
 */
