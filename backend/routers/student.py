"""Student-facing endpoints: profile, daily quiz, attempts, tests, concepts,
banners, leaderboard. The backend uses the service-role client and enforces
plan/ownership checks here in code."""
from collections import OrderedDict
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..deps import get_profile, effective_plan
from ..supabase_client import supabase

router = APIRouter(prefix="/api", tags=["student"])


def _settings() -> dict:
    res = supabase.table("app_settings").select("*").eq("id", 1).limit(1).execute()
    return res.data[0] if res.data else {
        "rank_visibility": {}, "banner_visibility": {},
    }


# ---------------------------------------------------------------- /me
@router.get("/me")
def me(profile: dict = Depends(get_profile)):
    plan = effective_plan(profile)
    s = _settings()
    key = "guest" if plan == "free" else plan
    return {
        "id": profile["id"],
        "full_name": profile.get("full_name"),
        "email": profile.get("email"),
        "state": profile.get("state"),
        "district": profile.get("district"),
        "target_exam": profile.get("target_exam"),
        "role": profile.get("role"),
        "plan": plan,
        "plan_expires_at": profile.get("plan_expires_at"),
        # the UI uses these to decide whether to render rank / banners
        "rank_visible": bool(s.get("rank_visibility", {}).get(key, True)),
        "banner_visible": bool(s.get("banner_visibility", {}).get(key, True)),
    }


# ---------------------------------------------------- /daily-quiz
@router.get("/daily-quiz")
def daily_quiz(profile: dict = Depends(get_profile)):
    """Today's free daily quiz, WITHOUT correct answers/explanations."""
    t = (supabase.table("tests").select("*")
         .eq("test_type", "daily").eq("is_published", True)
         .order("go_live_at", desc=True).limit(1).execute())
    if not t.data:
        raise HTTPException(404, "No daily quiz published today")
    test = t.data[0]
    return {"test": {"id": test["id"], "title": test["title"],
                     "duration_minutes": test["duration_minutes"]},
            "questions": _questions_for_test(test["id"], reveal=False)}


def _questions_for_test(test_id: str, reveal: bool):
    tq = (supabase.table("test_questions").select("question_id,question_order")
          .eq("test_id", test_id).order("question_order").execute())
    qids = [r["question_id"] for r in tq.data]
    if not qids:
        return []
    qs = supabase.table("questions").select("*").in_("id", qids).execute().data
    opts = (supabase.table("question_options").select("*")
            .in_("question_id", qids).order("option_order").execute().data)
    by_q = {q["id"]: q for q in qs}
    out = []
    for r in tq.data:
        q = by_q.get(r["question_id"])
        if not q:
            continue
        o = [op for op in opts if op["question_id"] == q["id"]]
        item = {
            "id": q["id"],
            "question": q["question_text"],
            "options": [{"id": op["id"], "text": op["option_text"]} for op in o],
        }
        if reveal:
            item["explanation"] = q.get("explanation")
            item["video_url"] = q.get("video_url")
            item["correct_option_id"] = next((op["id"] for op in o if op["is_correct"]), None)
        out.append(item)
    return out


# ------------------------------------------------------- /attempts (submit)
class AnswerIn(BaseModel):
    question_id: str
    selected_option_id: str | None = None


class SubmitIn(BaseModel):
    test_id: str
    answers: list[AnswerIn]
    time_taken_seconds: int | None = None


@router.post("/attempts")
def submit_attempt(body: SubmitIn, profile: dict = Depends(get_profile)):
    """Submit a whole test, grade it server-side, store the attempt + result,
    and return the solution (explanations/videos) — which the UI shows only
    after submission."""
    uid = profile["id"]
    # access check for premium tests
    test = supabase.table("tests").select("*").eq("id", body.test_id).limit(1).execute().data
    if not test:
        raise HTTPException(404, "Test not found")
    test = test[0]
    if not test["is_free"]:
        unlocked = {r["test_id"] for r in
                    supabase.table("user_test_access").select("test_id").eq("user_id", uid).execute().data}
        if not _test_owned(test, profile, unlocked):
            raise HTTPException(403, "This test is locked. Unlock or subscribe to attempt it.")

    # build a correct-answer map
    qids = [a.question_id for a in body.answers]
    opts = (supabase.table("question_options").select("id,question_id,is_correct")
            .in_("question_id", qids).execute().data)
    correct = {op["question_id"]: op["id"] for op in opts if op["is_correct"]}

    attempt = (supabase.table("attempts").upsert(
        {"test_id": body.test_id, "user_id": uid, "status": "submitted",
         "submitted_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="test_id,user_id").execute().data[0])

    correct_count = 0
    rows = []
    for a in body.answers:
        is_c = a.selected_option_id is not None and a.selected_option_id == correct.get(a.question_id)
        if is_c:
            correct_count += 1
        rows.append({"attempt_id": attempt["id"], "question_id": a.question_id,
                     "selected_option_id": a.selected_option_id,
                     "is_correct": is_c, "marks_awarded": 1 if is_c else 0})
    supabase.table("attempt_answers").upsert(rows, on_conflict="attempt_id,question_id").execute()

    total = len(body.answers)
    incorrect = sum(1 for a in body.answers if a.selected_option_id and not (
        a.selected_option_id == correct.get(a.question_id)))
    result = supabase.table("results").upsert({
        "attempt_id": attempt["id"], "user_id": uid, "test_id": body.test_id,
        "score": correct_count, "total_marks": total,
        "correct_count": correct_count, "incorrect_count": incorrect,
        "unattempted_count": total - correct_count - incorrect,
        "accuracy": round(100 * correct_count / total, 1) if total else 0,
        "time_taken_seconds": body.time_taken_seconds,
    }, on_conflict="attempt_id").execute().data[0]

    return {"score": correct_count, "total": total,
            "accuracy": result["accuracy"],
            # daily quiz solutions carry no video; premium tests do
            "solutions": _questions_for_test(body.test_id, reveal=True),
            "is_daily": test["test_type"] == "daily"}


# ---------------------------------------------------------------- /tests
PLAN_COVERS = {
    "free": set(),
    "weekly": {"weekly"},
    "monthly": {"weekly", "monthly"},
    "quarterly": {"weekly", "monthly", "quarterly"},
    "yearly": {"weekly", "monthly", "quarterly", "annual"},
}
UNLOCK_PAISE = {"weekly": 4900, "monthly": 9900, "quarterly": 39900, "annual": 99900, "daily": 0}
_PLAN_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}


def _plan_window(profile: dict):
    """Return (start, end, effective_plan). Window is the paid period."""
    plan = profile.get("plan", "free")
    exp = profile.get("plan_expires_at")
    if plan == "free" or not exp:
        return None, None, "free"
    try:
        end = datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except Exception:
        return None, None, "free"
    if end < datetime.now(timezone.utc):
        return None, None, "free"
    start = end - timedelta(days=_PLAN_DAYS.get(plan, 30))
    return start, end, plan


def _test_owned(test: dict, profile: dict, unlocked_ids: set) -> bool:
    if test.get("is_free"):
        return True
    if test["id"] in unlocked_ids:
        return True
    start, end, plan = _plan_window(profile)
    if plan == "free":
        return False
    if test["test_type"] not in PLAN_COVERS.get(plan, set()):
        return False
    gl = test.get("go_live_at")
    if not gl or not (start and end):
        return True
    try:
        g = datetime.fromisoformat(gl.replace("Z", "+00:00"))
    except Exception:
        return True
    return start <= g <= end


@router.get("/tests")
def list_tests(profile: dict = Depends(get_profile)):
    """All published tests grouped by month, with owned/locked per user."""
    plan = effective_plan(profile)
    uid = profile["id"]
    tests = (supabase.table("tests").select("*").eq("is_published", True)
             .neq("test_type", "daily").order("go_live_at", desc=True).execute().data)
    unlocked = {r["test_id"] for r in
                supabase.table("user_test_access").select("test_id").eq("user_id", uid).execute().data}
    done_ids = {r["test_id"] for r in
                supabase.table("results").select("test_id").eq("user_id", uid).execute().data}

    months: "OrderedDict[str, list]" = OrderedDict()
    for t in tests:
        owned = _test_owned(t, profile, unlocked)
        item = {
            "id": t["id"], "title": t["title"], "type": t["test_type"],
            "meta": f'{t.get("total_questions") or "?"} Q · {t["duration_minutes"]} min',
            "month": t.get("month") or "Other",
            "owned": owned, "completed": t["id"] in done_ids,
            "locked": (not owned),
            "unlock_paise": UNLOCK_PAISE.get(t["test_type"], 0),
            "go_live_at": t.get("go_live_at"),
        }
        months.setdefault(item["month"], []).append(item)

    return {"plan": plan,
            "months": [{"month": m, "tests": sorted(v, key=lambda x: (x["locked"], not x["completed"]))}
                       for m, v in months.items()]}


@router.get("/tests/{test_id}/quiz")
def test_quiz(test_id: str, profile: dict = Depends(get_profile)):
    t = supabase.table("tests").select("*").eq("id", test_id).limit(1).execute().data
    if not t:
        raise HTTPException(404, "Test not found")
    t = t[0]
    unlocked = {r["test_id"] for r in
                supabase.table("user_test_access").select("test_id").eq("user_id", profile["id"]).execute().data}
    if not _test_owned(t, profile, unlocked):
        raise HTTPException(403, "This test is locked.")
    return {"test": {"id": t["id"], "title": t["title"],
                     "duration_minutes": t["duration_minutes"], "test_type": t["test_type"]},
            "questions": _questions_for_test(test_id, reveal=False)}


@router.post("/tests/{test_id}/unlock")
def unlock_test(test_id: str, profile: dict = Depends(get_profile)):
    """Mock-pay to unlock a single locked test."""
    t = supabase.table("tests").select("*").eq("id", test_id).limit(1).execute().data
    if not t:
        raise HTTPException(404, "Test not found")
    t = t[0]
    price = UNLOCK_PAISE.get(t["test_type"], 0)
    now = datetime.now(timezone.utc)
    pay = supabase.table("payments").insert(
        {"user_id": profile["id"], "test_id": test_id, "amount_paise": price,
         "currency": "INR", "status": "paid", "method": "mock",
         "invoice_number": "MOCK-" + now.strftime("%Y%m%d%H%M%S")}).execute().data
    supabase.table("user_test_access").upsert(
        {"user_id": profile["id"], "test_id": test_id,
         "payment_id": (pay[0]["id"] if pay else None)},
        on_conflict="user_id,test_id").execute()
    return {"unlocked": True, "price_paise": price}


@router.get("/tests/{test_id}/result")
def test_result(test_id: str, profile: dict = Depends(get_profile)):
    """Stored result + solutions for a test the user has attempted."""
    res = (supabase.table("results").select("*").eq("user_id", profile["id"])
           .eq("test_id", test_id).limit(1).execute().data)
    if not res:
        raise HTTPException(404, "No attempt found")
    r = res[0]
    att = (supabase.table("attempts").select("id").eq("user_id", profile["id"])
           .eq("test_id", test_id).limit(1).execute().data)
    chosen = {}
    if att:
        ans = (supabase.table("attempt_answers").select("question_id,selected_option_id")
               .eq("attempt_id", att[0]["id"]).execute().data)
        chosen = {a["question_id"]: a["selected_option_id"] for a in ans}
    sols = _questions_for_test(test_id, reveal=True)
    for q in sols:
        q["your_option_id"] = chosen.get(q["id"])
    return {"score": r["score"], "total": r["total_marks"], "accuracy": r["accuracy"],
            "correct": r["correct_count"], "incorrect": r["incorrect_count"],
            "solutions": sols}


# ---------------------------------------------------------------- /concepts
@router.get("/concepts")
def list_concepts(profile: dict = Depends(get_profile)):
    plan = effective_plan(profile)
    rows = supabase.table("concepts").select("*").eq("is_active", True).order("created_at", desc=True).execute().data
    out = []
    for c in rows:
        access = c.get("access") or []
        viewable = "free" in access or plan in access
        out.append({
            "id": c["id"], "title": c["title"], "subject": c["subject"],
            "access": access, "viewable": viewable,
            # only hand over the video/notes if the user may watch it
            "video_url": c["video_url"] if viewable else None,
            "notes": c["notes"] if viewable else None,
        })
    return {"plan": plan, "concepts": out}


# ---------------------------------------------------------------- /banners
@router.get("/banners")
def list_banners(profile: dict = Depends(get_profile)):
    plan = effective_plan(profile)
    s = _settings()
    key = "guest" if plan == "free" else plan
    if not s.get("banner_visibility", {}).get(key, True):
        return {"banners": []}            # banners turned off for this tier
    rows = (supabase.table("banners").select("*").eq("is_active", True)
            .order("sort_order").execute().data)
    return {"banners": [{"id": b["id"], "image_url": b["image_url"],
                         "link_url": b.get("link_url"), "title": b.get("title")} for b in rows]}


# ---------------------------------------------------------------- /leaderboard
@router.get("/leaderboard")
def leaderboard(scope: str = "daily", profile: dict = Depends(get_profile)):
    plan = effective_plan(profile)
    s = _settings()
    key = "guest" if plan == "free" else plan
    if not s.get("rank_visibility", {}).get(key, True):
        raise HTTPException(403, "Ranking is currently disabled for your plan")
    # Most-recent test of the requested cadence, then its rankings.
    ttype = "daily" if scope == "daily" else scope
    t = (supabase.table("tests").select("id").eq("test_type", ttype)
         .order("go_live_at", desc=True).limit(1).execute().data)
    if not t:
        return {"rows": []}
    ranks = (supabase.table("rankings").select("national_rank,state,user_id")
             .eq("test_id", t[0]["id"]).order("national_rank").limit(50).execute().data)
    return {"rows": ranks}


# ---------------------------------------------------------------- /current-affairs
@router.get("/current-affairs")
def current_affairs(profile: dict = Depends(get_profile)):
    rows = (supabase.table("current_affairs").select("*").eq("is_published", True)
            .order("published_at", desc=True).limit(40).execute().data)
    return {"items": rows}


@router.get("/me/stats")
def my_stats(profile: dict = Depends(get_profile)):
    """Real dashboard numbers for the signed-in student. Everything is derived
    from their actual attempts/results/rankings — no mock values."""
    uid = profile["id"]
    results = (supabase.table("results").select("*").eq("user_id", uid)
               .order("created_at", desc=True).execute().data)
    attempted = len(results)
    accuracy = round(sum((r.get("accuracy") or 0) for r in results) / attempted, 1) if attempted else 0

    ranks = (supabase.table("rankings").select("*").eq("user_id", uid)
             .order("computed_at", desc=True).limit(1).execute().data)
    lr = ranks[0] if ranks else {}

    daily = (supabase.table("tests").select("id").eq("test_type", "daily")
             .eq("is_published", True).order("go_live_at", desc=True).limit(1).execute().data)
    daily_done = False
    if daily:
        dd = (supabase.table("results").select("id").eq("user_id", uid)
              .eq("test_id", daily[0]["id"]).execute().data)
        daily_done = bool(dd)

    recent = [{"score": r.get("score"), "total": r.get("total_marks"),
               "accuracy": r.get("accuracy"), "date": r.get("created_at")}
              for r in results[:8]][::-1]

    return {
        "tests_attempted": attempted,
        "accuracy": accuracy,
        "last_rank": lr.get("national_rank"),
        "last_percentile": lr.get("percentile"),
        "state_rank": lr.get("state_rank"),
        "district_rank": lr.get("district_rank"),
        "state": lr.get("state"),
        "daily_quiz_available": bool(daily),
        "daily_quiz_done": daily_done,
        "recent": recent,
    }


PLAN_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 90, "yearly": 365}
PLAN_PAISE = {"weekly": 4900, "monthly": 14900, "quarterly": 39900, "yearly": 99900}
PLAN_RANK = {"free": 0, "weekly": 1, "monthly": 2, "quarterly": 3, "yearly": 4}


class CheckoutIn(BaseModel):
    plan: str
    method: str | None = "mock"


@router.post("/checkout")
def checkout(body: CheckoutIn, profile: dict = Depends(get_profile)):
    """Mock payment: upgrades the user's plan immediately (no real gateway).
    Replace with Razorpay order + webhook verification for real money."""
    plan = body.plan
    if plan not in PLAN_DAYS:
        raise HTTPException(400, "Unknown plan")
    # don't allow downgrading to a lower/equal plan while one is active
    current = effective_plan(profile)
    if PLAN_RANK.get(plan, 0) <= PLAN_RANK.get(current, 0) and current != "free":
        raise HTTPException(400, "You already have this plan or a higher one.")
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=PLAN_DAYS[plan])
    supabase.table("profiles").update(
        {"plan": plan, "plan_expires_at": expires.isoformat()}
    ).eq("id", profile["id"]).execute()
    sub = supabase.table("subscriptions").insert(
        {"user_id": profile["id"], "plan": plan, "status": "active",
         "starts_at": now.isoformat(), "ends_at": expires.isoformat()}
    ).execute().data
    supabase.table("payments").insert(
        {"user_id": profile["id"], "subscription_id": (sub[0]["id"] if sub else None),
         "amount_paise": PLAN_PAISE[plan], "currency": "INR", "status": "paid",
         "method": body.method or "mock",
         "invoice_number": "MOCK-" + now.strftime("%Y%m%d%H%M%S")}
    ).execute()
    return {"plan": plan, "plan_expires_at": expires.isoformat()}
