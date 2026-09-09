"""Runtime configuration, read once from the environment.

Nothing here is mutable at runtime and nothing is cached in process memory
beyond the settings object itself (A5).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    environment: str
    demo_mode: bool
    session_secret: str
    cron_secret: str
    database_url: str
    sqlite_path: Path
    abr_guid: str
    demo_email_domain: str
    timezone: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cookies_secure(self) -> bool:
        """Every hosted environment is served over TLS (NFR-04), so the session
        cookie is marked secure everywhere except local development, where
        there is no certificate to satisfy it."""
        return self.environment != "development"

    @property
    def uses_postgres(self) -> bool:
        return self.database_url.startswith("postgres")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    environment = os.environ.get("APP_ENV", "development").strip().lower()
    # FR-152: demo mode is a single flag, and it is off in production
    # regardless of what the environment says (FR-158, §10.3).
    demo_mode = _flag("DEMO_MODE", default=environment != "production")
    if environment == "production":
        demo_mode = _flag("DEMO_MODE", default=False)

    sqlite_path = Path(
        os.environ.get("SQLITE_PATH", "")
        or (Path(os.environ.get("TMPDIR", "/tmp")) / "ab_verified.sqlite3"
            if os.environ.get("VERCEL")
            else ROOT / "var" / "ab_verified.sqlite3")
    )

    return Settings(
        environment=environment,
        demo_mode=demo_mode,
        session_secret=os.environ.get("SESSION_SECRET", "dev-only-insecure-secret"),
        cron_secret=os.environ.get("CRON_SECRET", "dev-only-cron-secret"),
        database_url=os.environ.get("DATABASE_URL", ""),
        sqlite_path=sqlite_path,
        abr_guid=os.environ.get("ABR_GUID", ""),
        demo_email_domain=os.environ.get("DEMO_EMAIL_DOMAIN", "demo.ab-verified.invalid"),
        timezone="Australia/Sydney",
    )
