"""FastAPI application factory.

Builds and configures the ASGI application. CORS is configured for the Vite
dev origin with ``allow_credentials=True`` so the browser sends and stores the
HTTP-only auth cookie on cross-origin API calls.

Feature routers (auth, source items, classification, knowledge, actions,
decisions, briefs, audit, and the Connected Workspace Intelligence module) are
registered in ``register_routers``, which keeps a single, discoverable wiring
point.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings


def register_routers(app: FastAPI) -> None:
    """Register API routers on the application.

    Every feature router is mounted under the ``/api`` prefix: the Core Engine
    surface (auth, source items, classification, knowledge, actions, decisions,
    business entities, briefs, audit) and the Connected Workspace Intelligence
    module (Google sign-in, integrations, Gmail, Calendar, documents, Copilot,
    email drafts, privacy).
    """

    from app.core.routers import (
        actions,
        audit,
        auth,
        briefs,
        business_entities,
        decisions,
        knowledge,
        source_items,
    )
    from app.modules.cwi.routers import calendar as cwi_calendar
    from app.modules.cwi.routers import copilot as cwi_copilot
    from app.modules.cwi.routers import documents as cwi_documents
    from app.modules.cwi.routers import email_drafts as cwi_email_drafts
    from app.modules.cwi.routers import gmail as cwi_gmail
    from app.modules.cwi.routers import google_auth as cwi_google_auth
    from app.modules.cwi.routers import integrations as cwi_integrations
    from app.modules.cwi.routers import privacy as cwi_privacy

    app.include_router(auth.router, prefix="/api")
    app.include_router(source_items.router, prefix="/api")
    app.include_router(knowledge.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(decisions.router, prefix="/api")
    app.include_router(business_entities.router, prefix="/api")
    app.include_router(briefs.router, prefix="/api")
    app.include_router(audit.router, prefix="/api")
    # Connected Workspace Intelligence (M6+): Google sign-in + integrations.
    app.include_router(cwi_google_auth.router, prefix="/api")
    app.include_router(cwi_integrations.router, prefix="/api")
    # Connected Workspace Intelligence (M6.2): Gmail sync & ingestion.
    app.include_router(cwi_gmail.router, prefix="/api")
    # Connected Workspace Intelligence (M6.3): Calendar from confirmed actions.
    app.include_router(cwi_calendar.router, prefix="/api")
    # Connected Workspace Intelligence (M6.4): Document upload & retrieval.
    app.include_router(cwi_documents.router, prefix="/api")
    # Connected Workspace Intelligence (M6.5): grounded Enterprise Copilot.
    app.include_router(cwi_copilot.router, prefix="/api")
    # Connected Workspace Intelligence (M6.6): AI-assisted Gmail draft.
    app.include_router(cwi_email_drafts.router, prefix="/api")
    # Connected Workspace Intelligence (M6.7): privacy, control & deletion.
    app.include_router(cwi_privacy.router, prefix="/api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure a FastAPI application instance."""

    settings = settings or get_settings()
    settings.validate_for_startup()

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    # CORS: allow the Vite dev origin. ``allow_credentials=True`` is required so
    # the browser will send/store the HTTP-only auth cookie on API calls.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.mode == "PRODUCTION":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    @app.get("/api/health", tags=["health"])
    def health() -> dict[str, str]:
        """Simple liveness probe used to confirm the app boots."""

        return {"status": "ok"}

    register_routers(app)
    from app.modules.cwi.routers import workspace, workspace_browser, source_proposals
    app.include_router(workspace.router, prefix="/api")
    app.include_router(workspace_browser.router, prefix="/api")
    app.include_router(source_proposals.router, prefix="/api")

    return app


# Module-level ASGI app for ``uvicorn app.main:app``.
app = create_app()
