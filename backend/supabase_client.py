"""Lazy service-role Supabase client.

Created on first use (not at import) so the app can boot in CI / health checks
without valid credentials. The service-role key bypasses RLS, so every endpoint
must enforce ownership / role checks itself (see deps.py and the routers).
"""
from supabase import create_client, Client
from .config import settings


class _LazySupabase:
    _client: Client | None = None

    def _get(self) -> Client:
        if _LazySupabase._client is None:
            _LazySupabase._client = create_client(
                settings.supabase_url, settings.supabase_service_key
            )
        return _LazySupabase._client

    def __getattr__(self, name):
        return getattr(self._get(), name)


supabase = _LazySupabase()
