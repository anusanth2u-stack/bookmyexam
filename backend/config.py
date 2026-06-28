"""Configuration loaded from environment variables / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str
    supabase_service_key: str           # service_role key — server only, NEVER ship to the browser
    supabase_anon_key: str = ""         # passed to the frontend for Supabase Auth
    supabase_jwt_secret: str = ""            # only needed for legacy HS256 projects

    # App
    frontend_origin: str = "*"          # CORS; set to your domain in production
    serve_frontend: bool = True         # serve /frontend/index.html at "/"

    # Razorpay (optional until you wire payments)
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""


settings = Settings()
