"""Application configuration.

Settings are loaded from environment variables and an optional ``.env`` file
via ``pydantic-settings``. See ``.env.example`` for the full documented set.

Authentication uses a JWT delivered as an **HTTP-only cookie**: client-side
JavaScript never reads the token, and the browser returns it automatically on
same-site requests. The JWT and cookie settings below configure signing, token
lifetime, and the cookie's name / ``Secure`` / ``SameSite`` attributes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProductionConfigurationError(RuntimeError):
    """Raised when a production process would start with unsafe settings."""


class Settings(BaseSettings):
    """Strongly-typed application settings loaded from the environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- General -------------------------------------------------------------
    app_name: str = "KnowAct"
    # Runtime mode. Governs the AI provider failure policy (see design):
    #   DEVELOPMENT -> LLM failures may fall back to the deterministic mock.
    #   PRODUCTION  -> LLM failures surface as a retriable 503 (no silent fallback).
    mode: Literal["DEVELOPMENT", "PRODUCTION"] = "DEVELOPMENT"

    # --- Database ------------------------------------------------------------
    # SQLAlchemy / psycopg connection URL. The real password is supplied only
    # via the local .env file (DATABASE_URL); this default carries a non-secret
    # CHANGE_ME placeholder so no credential ever lives in source. The database
    # name (knowact) and user (knowact_app) match the documented local Docker
    # setup (host port 5434), e.g.
    #   postgresql+psycopg://knowact_app:<password>@localhost:5434/knowact
    # In a managed deployment (e.g. Neon) set the full provider URL, keeping the
    # ``postgresql+psycopg://`` scheme and ``?sslmode=require``.
    database_url: str = (
        "postgresql+psycopg://knowact_app:CHANGE_ME@localhost:5434/knowact"
    )

    # --- Seed data -----------------------------------------------------------
    # Password assigned to the demo users created by ``python -m app.seed``.
    # Deliberately has NO default: the seeder refuses to run until it is set, so
    # a shared well-known password can never be baked into the repository or a
    # deployed environment.
    seed_user_password: str | None = None

    # --- Authentication (JWT bearer tokens) ----------------------------------
    # Secret used to sign JWTs. MUST be overridden in production.
    jwt_secret: str = Field(
        default="change-me-in-production",
        description="Secret key used to sign JWT bearer tokens.",
    )
    jwt_algorithm: Literal["HS256"] = "HS256"
    # Access-token lifetime in minutes.
    access_token_expire_minutes: int = Field(default=60 * 24, gt=0, le=60 * 24 * 30)

    # --- Auth cookie (HTTP-only) ---------------------------------------------
    # The signed JWT is delivered to the browser as an HTTP-only cookie so that
    # client-side JavaScript can never read it (mitigates token theft via XSS).
    # The browser sends it back automatically on same-site requests.
    auth_cookie_name: str = "knowact_access_token"
    # ``Secure`` requires HTTPS. Keep False for local http://localhost dev and
    # set True in any deployed (HTTPS) environment.
    auth_cookie_secure: bool = False
    # ``SameSite`` policy. "lax" gives baseline CSRF protection while still
    # allowing top-level navigations; the SPA calls the API same-site via the
    # Vite dev proxy, so "lax" is sufficient here.
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Exact account allowlist for this phase-one, single-user deployment.
    # Local development may leave this empty; PRODUCTION fails startup unless
    # exactly one non-empty address is configured. When present, the same list
    # is enforced for password login and Google Sign-In.
    auth_allowed_emails: list[str] = Field(default_factory=list)

    # --- AI provider ---------------------------------------------------------
    # "mock" (default, deterministic, no key) or "llm" (optional external LLM).
    ai_provider: Literal["mock", "llm"] = "mock"
    # Only used when ai_provider == "llm".
    llm_api_key: str | None = None
    # Chat model used by the real LLMProvider (only when ai_provider == "llm").
    llm_model: str = "gpt-4o-mini"
    # Optional base URL override for OpenAI-compatible endpoints. When None the
    # official OpenAI endpoint is used.
    llm_base_url: str | None = None
    # Request timeout (seconds) for LLM calls.
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    # Upper bound on tokens the LLM may generate per structured-output call.
    llm_max_output_tokens: int = Field(default=1500, gt=0, le=16_384)

    # --- CORS ----------------------------------------------------------------
    # Origins allowed to call the API. Defaults to the Vite dev server.
    cors_origins: list[str] = ["http://localhost:5173"]

    # --- Frontend (SPA) ------------------------------------------------------
    # Public origin of the single-page app. After Google redirects the browser
    # back to a backend OAuth callback, the callback issues a 302 redirect to a
    # route under this base so the user lands back in the SPA (not on a JSON
    # page). Defaults to the Vite dev server.
    frontend_base_url: str = "http://localhost:5173"

    # --- Connected Workspace Intelligence: Google OAuth ----------------------
    # Google OAuth client credentials for Google Sign-In (OpenID Connect) and
    # incremental Gmail/Calendar authorization. These are supplied only via the
    # local .env; the repository carries placeholders. All CWI tests use a fake
    # Google transport, so these may stay unset for local development.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    # Base URL the OAuth redirect/callback endpoints are built against (the
    # public origin of this backend). Google appends its ``code``/``state`` to a
    # callback path under this base.
    google_oauth_redirect_base: str = "http://localhost:8000"

    # --- Connected Workspace Intelligence: token encryption ------------------
    # Symmetric key (Fernet) used by the TokenVault to encrypt OAuth access and
    # refresh tokens at rest. When unset the TokenVault raises a clear error on
    # use rather than silently storing plaintext. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str | None = None

    # --- Connected Workspace Intelligence: document storage ------------------
    # Where uploaded document binaries live. "local" keeps them on the local
    # filesystem under ``storage_root`` (never in Postgres).
    storage_backend: Literal["local"] = "local"
    storage_root: str = "./var/storage"

    # --- Connected Workspace Intelligence: embeddings ------------------------
    # "mock" (default, deterministic, no key) produces reproducible vectors for
    # local development and tests; "openai" uses the OpenAI embeddings API.
    embedding_provider: Literal["mock", "openai"] = "mock"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = Field(default=1536, gt=0, le=4096)
    # API key for the embeddings provider. Only used when embedding_provider
    # (or ai_provider) routes through OpenAI; unset for the deterministic mock.
    openai_api_key: str | None = None

    @staticmethod
    def normalize_auth_email(email: str) -> str:
        """Return the canonical form used for exact allowlist comparisons."""

        return (email or "").strip().casefold()

    def normalized_auth_allowed_emails(self) -> frozenset[str]:
        """Return configured, non-empty account addresses in canonical form."""

        return frozenset(
            normalized
            for email in self.auth_allowed_emails
            if (normalized := self.normalize_auth_email(email))
        )

    def is_auth_email_allowed(self, email: str) -> bool:
        """Apply the account allowlist to every interactive login method.

        An empty list is convenient for local development and tests. Production
        can never reach this permissive branch because :meth:`validate_for_startup`
        requires exactly one configured owner address.
        """

        allowed = self.normalized_auth_allowed_emails()
        if not allowed:
            return self.mode != "PRODUCTION"
        return self.normalize_auth_email(email) in allowed

    def validate_for_startup(self) -> None:
        """Fail closed when a production deployment is missing real secrets.

        Development deliberately keeps its mock providers and localhost
        defaults. Production must never report healthy while signing tokens
        with the repository placeholder or silently serving fake AI/Google
        behavior.
        """

        if self.mode != "PRODUCTION":
            return

        errors: list[str] = []
        secret_bytes = self.jwt_secret.encode("utf-8")
        if self.jwt_secret == "change-me-in-production" or len(secret_bytes) < 32:
            errors.append("JWT_SECRET must be a non-default secret of at least 32 bytes")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true")
        normalized_allowed_emails = self.normalized_auth_allowed_emails()
        if (
            len(self.auth_allowed_emails) != 1
            or len(normalized_allowed_emails) != 1
        ):
            errors.append(
                "AUTH_ALLOWED_EMAILS must contain exactly one non-empty owner email"
            )
        if "CHANGE_ME" in self.database_url or not self.database_url.startswith(
            ("postgresql://", "postgresql+psycopg://")
        ):
            errors.append("DATABASE_URL must be a configured PostgreSQL URL")
        if self.ai_provider != "llm" or not self.llm_api_key:
            errors.append("AI_PROVIDER=llm and LLM_API_KEY are required")
        if self.embedding_provider != "openai" or not self.openai_api_key:
            errors.append(
                "EMBEDDING_PROVIDER=openai and OPENAI_API_KEY are required"
            )
        if not self.google_oauth_client_id or not self.google_oauth_client_secret:
            errors.append("Google OAuth client credentials are required")
        if not self.token_encryption_key:
            errors.append("TOKEN_ENCRYPTION_KEY is required")
        else:
            try:
                Fernet(self.token_encryption_key.encode("utf-8"))
            except (TypeError, ValueError):
                errors.append("TOKEN_ENCRYPTION_KEY must be a valid Fernet key")
        if not self.frontend_base_url.startswith("https://"):
            errors.append("FRONTEND_BASE_URL must use HTTPS")
        if not self.google_oauth_redirect_base.startswith("https://"):
            errors.append("GOOGLE_OAUTH_REDIRECT_BASE must use HTTPS")
        if any(
            origin == "*" or not origin.startswith("https://")
            for origin in self.cors_origins
        ):
            errors.append("CORS_ORIGINS must contain only explicit HTTPS origins")

        if errors:
            raise ProductionConfigurationError(
                "Unsafe production configuration: " + "; ".join(errors)
            )


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Cached so the ``.env`` file and environment are read once per process.
    """

    return Settings()
