"""Auth dependencies.

Verifies Supabase access tokens. Supabase's new projects sign JWTs with
asymmetric JWT Signing Keys (RS256/ES256), so we verify against the project's
public JWKS endpoint. This also still accepts legacy HS256 tokens if your
project uses the shared secret (set SUPABASE_JWT_SECRET in that case).
"""
from fastapi import Depends, HTTPException, Header
from jose import jwt, jwk
from jose.utils import base64url_decode
import httpx

from .config import settings
from .supabase_client import supabase

# cache of public keys fetched from Supabase, keyed by "kid"
_JWKS: dict = {}


def _jwks() -> dict:
    global _JWKS
    if not _JWKS:
        url = settings.supabase_url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
        try:
            data = httpx.get(url, timeout=10).json()
            _JWKS = {k["kid"]: k for k in data.get("keys", [])}
        except Exception:
            _JWKS = {}
    return _JWKS


def get_token(authorization: str = Header(None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    return authorization.split(" ", 1)[1]


def get_current_user(token: str = Depends(get_token)) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise HTTPException(401, "Malformed token")

    alg = header.get("alg", "")
    try:
        if alg == "HS256":
            # legacy shared-secret projects
            payload = jwt.decode(
                token, settings.supabase_jwt_secret,
                algorithms=["HS256"], audience="authenticated",
            )
        else:
            # new asymmetric signing keys (RS256 / ES256) -> verify with JWKS
            kid = header.get("kid")
            keys = _jwks()
            key = keys.get(kid)
            if key is None:                      # refresh once (keys may have rotated)
                _JWKS.clear()
                key = _jwks().get(kid)
            if key is None:
                raise HTTPException(401, "Signing key not found")
            public_key = jwk.construct(key, alg)
            message, sig = token.rsplit(".", 1)
            if not public_key.verify(message.encode(), base64url_decode(sig.encode())):
                raise HTTPException(401, "Bad signature")
            payload = jwt.get_unverified_claims(token)
            if payload.get("aud") not in ("authenticated", None):
                raise HTTPException(401, "Wrong audience")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

    return {"id": payload["sub"], "email": payload.get("email")}


def get_profile(user: dict = Depends(get_current_user)) -> dict:
    res = supabase.table("profiles").select("*").eq("id", user["id"]).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Profile not found")
    return res.data[0]


def effective_plan(profile: dict) -> str:
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
