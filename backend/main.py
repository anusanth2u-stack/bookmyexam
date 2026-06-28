"""Bookmyexam.in API — FastAPI entrypoint."""
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .routers import student, admin

app = FastAPI(title="Bookmyexam.in API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin] if settings.frontend_origin != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(student.router)
app.include_router(admin.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def public_config():
    """Safe values the browser needs to initialise Supabase Auth.
    The anon key is public by design; the service key is never exposed."""
    return {
        "supabase_url": settings.supabase_url,
        "supabase_anon_key": settings.supabase_anon_key,
        "razorpay_key_id": settings.razorpay_key_id,
    }


# ---- serve the single-page frontend (optional) ----
FRONTEND = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if settings.serve_frontend and os.path.isdir(FRONTEND):
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(FRONTEND, "index.html"))
