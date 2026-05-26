"""
TPBank-specific configuration.

All env vars use the TPBANK_ prefix, e.g.:
    TPBANK_PUBLIC_KEY_FILE=certs/tpbank_public.pem
    TPBANK_RATE_LIMIT_REQUESTS=100
    TPBANK_RATE_LIMIT_WINDOW=60

When a per-bank value is 0 / empty, the handler falls back to the global
defaults defined in app.config.settings.Settings.
"""
from pydantic_settings import BaseSettings


class TPBankSettings(BaseSettings):
    public_key_file: str = "certs/tpbank_public.pem"
    rate_limit_enabled: bool | None = None  # None = fall back to global default
    rate_limit_requests: int = 0   # 0 = fall back to global default
    rate_limit_window: int = 0     # 0 = fall back to global default

    model_config = {
        "env_prefix": "TPBANK_",
        "env_file": ".env",
        "case_sensitive": False,
    }


tpbank_settings = TPBankSettings()
