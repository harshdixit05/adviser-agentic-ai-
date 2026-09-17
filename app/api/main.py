"""
The HTTP surface: one endpoint the website's chat widget talks to.

It is thin on purpose. Everything that decides what may be said lives in the
graph and its grounding gate; this layer only handles who may ask, how often,
and where a conversation is kept.

Run it with:

    uvicorn app.api.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.agent.conversation import Advisor
from app.agent.model import ModelNotConfigured, build_model
from app.api.rate_limit import RateLimiter
from app.api.schemas import ChatRequest, ChatResponse, HealthResponse
from app.api.sessions import SessionStore
from app.core.config import get_settings
from app.db.models import Course
from app.db.session import SessionFactory

log = logging.getLogger(__name__)

settings = get_settings()

sessions = SessionStore(
    ttl_minutes=settings.session_ttl_minutes,
    max_sessions=settings.max_sessions,
)
per_session = RateLimiter(settings.messages_per_session_per_hour)
per_client = RateLimiter(settings.messages_per_ip_per_hour)

# Populated at startup. None means the service is up but cannot answer, which
# is a more useful state than refusing to boot: /health can then say why.
_model = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model
    try:
        _model = build_model()
    except ModelNotConfigured as error:
        log.warning("Starting without a model: %s", error)
        _model = None
    yield
    _model = None


app = FastAPI(
    title="Intellimindz Learning Advisor",
    version="0.1.0",
    lifespan=lifespan,
    # The docs pages are useful in development and are attack surface in
    # production; leave them behind the same origin policy as everything else.
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    # No cookies and no Authorization header: a conversation is identified by
    # an opaque id in the body, so the browser never needs credentialled CORS.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)


def client_key(request: Request) -> str:
    """
    The address to rate-limit by.

    X-Forwarded-For is ignored: any caller can set it, so trusting it hands
    every client an unlimited supply of identities. Behind a reverse proxy,
    run uvicorn with --proxy-headers and --forwarded-allow-ips set to that
    proxy, which makes request.client.host the real address.
    """
    return request.client.host if request.client else "unknown"


def require_model():
    if _model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The advisor is not configured on this deployment yet.",
        )
    return _model


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    with SessionFactory() as session:
        courses = session.scalar(select(func.count()).select_from(Course)) or 0

    return HealthResponse(
        status="ok" if courses and _model is not None else "degraded",
        catalogue_courses=courses,
        model_configured=_model is not None,
        active_sessions=len(sessions),
    )


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request, model=Depends(require_model)) -> ChatResponse:
    address = client_key(request)
    if not per_client.check(address):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many messages. Please try again shortly.",
            headers={"Retry-After": str(per_client.retry_after(address))},
        )

    session_id, advisor = sessions.get_or_create(payload.session_id, lambda: Advisor(model))

    if not per_session.check(session_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This conversation has hit its limit. Please start a new one.",
            headers={"Retry-After": str(per_session.retry_after(session_id))},
        )

    try:
        turn = advisor.ask(payload.message)
    except Exception:
        # The learner gets a sentence; the detail goes to the log. An exception
        # string can carry a prompt, a row, or the tail of an API key.
        log.exception("chat turn failed for session %s", session_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="I couldn't answer that just now. Please try again.",
        ) from None

    return ChatResponse(
        session_id=session_id,
        answer=turn.answer,
        sources=turn.tools_used,
        withheld=turn.was_refused,
    )


@app.post("/api/chat/{session_id}/end", status_code=status.HTTP_204_NO_CONTENT)
def end(session_id: str) -> None:
    """Lets the widget drop a conversation when the learner closes it."""
    sessions.forget(session_id)
