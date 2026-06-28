"""Student-facing endpoints: profile, daily quiz, attempts, tests, concepts,
banners, leaderboard. The backend uses the service-role client and enforces
plan/ownership checks here in code."""
from collections import OrderedDict
from datetime import datetime, timezone

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
    today = datetime.now(timezone.utc).date().isoformat()
    t = (supabase.table("tests").select("*")
         .eq("test_type", "daily").eq("is_published", True)
         .gte("go_live_at", today).order("go_live_at", desc=True).limit(1).execute())
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
    if not test["is_free"] and not _has_access(uid, test, effective_plan(profile)):
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
@router.get("/tests")
def list_tests(profile: dict = Depends(get_profile)):
    """All published tests grouped by month, with owned/locked per user."""
    plan = effective_plan(profile)
    uid = profile["id"]
    tests = (supabase.table("tests").select("*").eq("is_published", True)
             .neq("test_type", "daily").order("go_live_at", desc=True).execute().data)
    owned_ids = {r["test_id"] for r in
                 supabase.table("user_test_access").select("test_id").eq("user_id", uid).execute().data}
    done_ids = {r["test_id"] for r in
                supabase.table("results").select("test_id").eq("user_id", uid).execute().data}

    months: "OrderedDict[str, list]" = OrderedDict()
    for t in tests:
        owned = t["id"] in owned_ids or _has_access(uid, t, plan)
        item = {
            "id": t["id"], "title": t["title"], "type": t["test_type"],
            "meta": f'{t.get("total_questions") or "?"} Q · {t["duration_minutes"]} min',
            "month": t.get("month") or "Other",
            "owned": owned, "completed": t["id"] in done_ids,
            "locked": (not owned) and (not t["is_free"]),
            "price_paise": t["price_paise"],
            "go_live_at": t.get("go_live_at"),
        }
        months.setdefault(item["month"], []).append(item)

    return {"plan": plan,
            "months": [{"month": m, "tests": sorted(v, key=lambda x: (x["locked"], not x["owned"]))}
                       for m, v in months.items()]}


def _has_access(uid: str, test: dict, plan: str) -> bool:
    """Does this user currently have access to this test?
    Free tests are open. A paid plan covers tests live within the plan window
    (simplified here: any non-free plan covers all non-free tests; tighten with
    plan_expires_at vs test.go_live_at in production)."""
    if test["is_free"]:
        return True
    if plan != "free":
        return True
    return False


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
