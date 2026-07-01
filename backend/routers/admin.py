"""Admin endpoints — all guarded by require_admin. Mirrors the configurator
in the prototype: create tests (manual or bulk), upload concepts with access
tiers, manage banners, and toggle rank/banner visibility per plan."""
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..deps import require_admin
from ..supabase_client import supabase

router = APIRouter(prefix="/api/admin", tags=["admin"])

YT = re.compile(r"(?:youtu\.be/|v=|embed/)([\w-]{11})")


def yt_id(url: str | None) -> str | None:
    if not url:
        return None
    m = YT.search(url)
    if m:
        return m.group(1)
    return url.strip() if len(url.strip()) == 11 else None


# --------------------------------------------------------- settings
class Visibility(BaseModel):
    rank_visibility: dict | None = None
    banner_visibility: dict | None = None


@router.put("/settings")
def update_settings(body: Visibility, _: dict = Depends(require_admin)):
    patch = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if body.rank_visibility is not None:
        patch["rank_visibility"] = body.rank_visibility
    if body.banner_visibility is not None:
        patch["banner_visibility"] = body.banner_visibility
    supabase.table("app_settings").update(patch).eq("id", 1).execute()
    return {"ok": True}


# --------------------------------------------------------- banners
class BannerIn(BaseModel):
    image_url: str
    link_url: str | None = None
    title: str | None = None
    sort_order: int = 0


@router.post("/banners")
def add_banner(b: BannerIn, _: dict = Depends(require_admin)):
    row = supabase.table("banners").insert(b.model_dump()).execute().data[0]
    return row


@router.delete("/banners/{banner_id}")
def delete_banner(banner_id: str, _: dict = Depends(require_admin)):
    supabase.table("banners").delete().eq("id", banner_id).execute()
    return {"ok": True}


# --------------------------------------------------------- concepts
class ConceptIn(BaseModel):
    title: str
    subject: str | None = None
    video_url: str
    notes: str | None = None
    access: list[str] = ["free"]
    material_url: str | None = None


@router.post("/concepts")
def add_concept(c: ConceptIn, admin: dict = Depends(require_admin)):
    vid = yt_id(c.video_url) or c.video_url
    row = supabase.table("concepts").insert({
        "title": c.title, "subject": c.subject, "video_url": vid,
        "notes": c.notes, "access": c.access, "material_url": c.material_url,
        "created_by": admin["id"],
    }).execute().data[0]
    return row


@router.delete("/concepts/{concept_id}")
def delete_concept(concept_id: str, _: dict = Depends(require_admin)):
    supabase.table("concepts").delete().eq("id", concept_id).execute()
    return {"ok": True}


# --------------------------------------------------------- current affairs
class AffairIn(BaseModel):
    category: str
    title: str
    body: str
    published_at: str | None = None
    image_url: str | None = None


@router.post("/affairs")
def add_affair(a: AffairIn, admin: dict = Depends(require_admin)):
    row = supabase.table("current_affairs").insert({
        "category": a.category, "title": a.title, "body": a.body,
        "image_url": a.image_url,
        "published_at": a.published_at or datetime.now(timezone.utc).date().isoformat(),
        "created_by": admin["id"],
    }).execute().data[0]
    return row


# --------------------------------------------------------- create test (manual or bulk)
class QuestionIn(BaseModel):
    question: str
    options: list[str]          # exactly 4
    correct_index: int          # 0..3
    explanation: str | None = None
    video_url: str | None = None
    subject: str | None = None


class CreateTestIn(BaseModel):
    title: str
    test_type: str              # daily | weekly | monthly | quarterly | annual
    duration_minutes: int = 30
    go_live_at: str | None = None
    month: str | None = None
    is_free: bool = False
    price_paise: int = 0
    questions: list[QuestionIn]


@router.post("/tests")
def create_test(body: CreateTestIn, admin: dict = Depends(require_admin)):
    """Create a test and its questions. Uses BULK inserts (4 DB calls total)
    instead of one-per-question, so large tests don't time out on free tiers."""
    if not body.questions:
        raise HTTPException(400, "Add at least one question.")
    is_daily = body.test_type == "daily"
    go_live = body.go_live_at or datetime.now(timezone.utc).isoformat()
    if len(go_live) == 10:                       # date-only -> add a time
        go_live = go_live + "T00:00:00+00:00"
    try:
        test = supabase.table("tests").insert({
            "title": body.title, "test_type": body.test_type,
            "duration_minutes": body.duration_minutes,
            "total_questions": len(body.questions),
            "is_free": is_daily or body.is_free,
            "price_paise": body.price_paise,
            "month": body.month or datetime.now(timezone.utc).strftime("%Y-%m"),
            "go_live_at": go_live,
            "is_published": True,
            "created_by": admin["id"],
        }).execute().data[0]

        qrows, optrows, tqrows = [], [], []
        for order, q in enumerate(body.questions):
            qid = str(uuid.uuid4())
            qrows.append({
                "id": qid, "question_text": q.question,
                "explanation": q.explanation,
                "video_url": (None if is_daily else yt_id(q.video_url)),
                "subject": q.subject,
                "created_by": admin["id"],
            })
            for i, text in enumerate(q.options):
                optrows.append({"question_id": qid, "option_text": text,
                                "is_correct": (i == q.correct_index), "option_order": i})
            tqrows.append({"test_id": test["id"], "question_id": qid,
                           "question_order": order, "marks": 1})

        supabase.table("questions").insert(qrows).execute()
        supabase.table("question_options").insert(optrows).execute()
        supabase.table("test_questions").insert(tqrows).execute()
    except HTTPException:
        raise
    except Exception as e:                       # surface the real DB error
        raise HTTPException(500, f"Could not create test: {e}")

    return {"test_id": test["id"], "questions": len(body.questions)}


@router.delete("/affairs/{affair_id}")
def delete_affair(affair_id: str, _: dict = Depends(require_admin)):
    supabase.table("current_affairs").delete().eq("id", affair_id).execute()
    return {"ok": True}


@router.post("/compute-ranks")
def compute_ranks(_: dict = Depends(require_admin)):
    """Compute ranks now for every test that has any results (demo helper,
    since the free tier has no scheduled cron)."""
    tids = {r["test_id"] for r in supabase.table("results").select("test_id").execute().data}
    done = 0
    for tid in tids:
        try:
            supabase.rpc("compute_test_ranks", {"p_test_id": tid}).execute()
            done += 1
        except Exception:
            pass
    return {"computed": done}


@router.delete("/tests/{test_id}")
def delete_test(test_id: str, _: dict = Depends(require_admin)):
    """Remove a test (its questions/options/attempts cascade via FKs)."""
    supabase.table("tests").delete().eq("id", test_id).execute()
    return {"ok": True}


@router.get("/tests")
def list_all_tests(_: dict = Depends(require_admin)):
    """All tests for the admin manage list."""
    rows = (supabase.table("tests").select("id,title,test_type,total_questions,go_live_at,month")
            .order("go_live_at", desc=True).execute().data)
    return {"tests": rows}
