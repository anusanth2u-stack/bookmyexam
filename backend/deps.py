"""Auth dependencies.

The frontend signs in with Supabase Auth (Google / email) and sends the
resulting access token as `Authorization: Bearer <token>`. We verify it with
the project's JWT secret — no extra round-trip to Supabase needed.
"""
from fastapi import Depends, HTTPException, Header
from jose import jwt, JWTError

from .config import settings
from .supabase_client import supabase


def get_token(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    return authorization.split(" ", 1)[1]


def get_current_user(token: str = Depends(get_token)) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")
    return {"id": payload["sub"], "email": payload.get("email")}


def get_profile(user: dict = Depends(get_current_user)) -> dict:
    res = supabase.table("profiles").select("*").eq("id", user["id"]).limit(1).execute()
    if not res.data:
        # Should have been created by the on_auth_user_created trigger.
        raise HTTPException(404, "Profile not found")
    return res.data[0]


def effective_plan(profile: dict) -> str:
    """Plan, downgraded to 'free' if the subscription has expired."""
    from datetime import datetime, timezone
    plan = profile.get("plan", "free")
    exp = profile.get("plan_expires_at")
    if plan != "free" and exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return "free"
        except Exception:
            pass
    return plan


def require_admin(profile: dict = Depends(get_profile)) -> dict:
    if profile.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return profile
