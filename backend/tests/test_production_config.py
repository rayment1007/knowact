"""Production settings must fail closed instead of selecting mock providers."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import ProductionConfigurationError, Settings
from app.main import create_app


def _production_settings(**overrides) -> Settings:
    values = {
        "mode": "PRODUCTION",
        "database_url": "postgresql+psycopg://user:secret@db.example/knowact",
        "jwt_secret": "a-production-secret-that-is-at-least-32-bytes-long",
        "auth_cookie_secure": True,
        "auth_allowed_emails": ["owner@example.com"],
        "ai_provider": "llm",
        "llm_api_key": "llm-key",
        "embedding_provider": "openai",
        "openai_api_key": "embedding-key",
        "google_oauth_client_id": "client-id",
        "google_oauth_client_secret": "client-secret",
        "token_encryption_key": Fernet.generate_key().decode("ascii"),
        "frontend_base_url": "https://knowact.example",
        "google_oauth_redirect_base": "https://api.knowact.example",
        "cors_origins": [],
    }
    values.update(overrides)
    return Settings(**values)


def test_default_production_settings_are_rejected() -> None:
    with pytest.raises(ProductionConfigurationError) as exc:
        Settings(
            mode="PRODUCTION",
            database_url=(
                "postgresql+psycopg://knowact_app:CHANGE_ME@localhost:5434/knowact"
            ),
            jwt_secret="change-me-in-production",
            auth_cookie_secure=False,
            ai_provider="mock",
            llm_api_key=None,
            embedding_provider="mock",
            openai_api_key=None,
            google_oauth_client_id=None,
            google_oauth_client_secret=None,
            token_encryption_key=None,
            frontend_base_url="http://localhost:5173",
            google_oauth_redirect_base="http://localhost:8000",
            cors_origins=["http://localhost:5173"],
        ).validate_for_startup()
    message = str(exc.value)
    assert "JWT_SECRET" in message
    assert "DATABASE_URL" in message
    assert "TOKEN_ENCRYPTION_KEY" in message
    assert "AUTH_ALLOWED_EMAILS" in message


@pytest.mark.parametrize(
    "override",
    [
        {"jwt_secret": "too-short"},
        {"auth_cookie_secure": False},
        {"auth_allowed_emails": []},
        {"auth_allowed_emails": ["one@example.com", "two@example.com"]},
        {"ai_provider": "mock"},
        {"embedding_provider": "mock"},
        {"google_oauth_client_secret": None},
        {"token_encryption_key": "not-a-fernet-key"},
        {"frontend_base_url": "http://knowact.example"},
        {"google_oauth_redirect_base": "http://api.knowact.example"},
        {"cors_origins": ["*"]},
    ],
)
def test_each_unsafe_production_override_is_rejected(override: dict) -> None:
    with pytest.raises(ProductionConfigurationError):
        _production_settings(**override).validate_for_startup()


def test_complete_production_settings_boot_application() -> None:
    settings = _production_settings()
    settings.validate_for_startup()
    app = create_app(settings)
    assert app.title == "KnowAct"
