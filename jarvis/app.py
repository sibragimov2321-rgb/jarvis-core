import hashlib
import json
import secrets
import time
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from .commands import DANGEROUS, Command, execute, plan
from .config import Settings
from .providers import Providers, segment
from .store import Store


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=30, max_length=200)
    approve: bool = Field(strict=True)


class MemoryAppend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    text: str = Field(min_length=1, max_length=20000)
    expected_revision: str = Field(min_length=1, max_length=1000)


def create_app(settings=None, providers=None):
    settings = settings or Settings()
    store = Store(settings)
    p = providers or Providers(settings, store)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if isinstance(p, Providers):
            p.http.close()

    app = FastAPI(title="JARVIS Core", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.providers = store, p
    bearer = HTTPBearer(auto_error=False)

    def auth(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
        if credentials is None or not secrets.compare_digest(
            credentials.credentials, settings.jarvis_api_key.get_secret_value()
        ):
            raise HTTPException(401, "Invalid bearer token", headers={"WWW-Authenticate": "Bearer"})

    secure = [Depends(auth)]

    def confirmation_context():
        token = store.get("google_token") or {}
        return {
            "google_connection": token.get("connection_id"),
            "calendar": settings.google_calendar_id,
            "tasklist": settings.google_tasklist_id,
            "source": settings.jules_source,
            "branch": settings.jules_branch,
        }

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/health")
    def health():
        store.get("health")
        return {"status": "ok"}

    @app.get("/status", dependencies=secure)
    def status():
        return {
            "google_connected": store.get("google_token") is not None,
            "memory_configured": bool(settings.jarvis_memory_id),
            "jules_configured": bool(settings.jules_api_key.get_secret_value() and settings.jules_source),
            "conversation_configured": bool(
                settings.gemini_api_key.get_secret_value() and settings.gemini_model
            ),
        }

    @app.post("/auth/google/start", dependencies=secure)
    def oauth_start(response: Response):
        url = p.oauth_start()
        # Bind callback to the initiating browser in addition to state + PKCE.
        from urllib.parse import parse_qs, urlparse

        state = parse_qs(urlparse(url).query)["state"][0]
        response.set_cookie(
            "jarvis_oauth",
            state,
            httponly=True,
            samesite="lax",
            max_age=600,
            secure=settings.google_redirect_uri.startswith("https://"),
            path="/auth/google",
        )
        return {"authorization_url": url}

    from fastapi import Request

    @app.get("/auth/google/callback")
    def oauth_callback(request: Request, response: Response, state: str, code: str = "", error: str = ""):
        if not secrets.compare_digest(request.cookies.get("jarvis_oauth", ""), state) or not state:
            raise HTTPException(400, "OAuth browser binding mismatch; restart in this browser")
        if error or not code:
            raise HTTPException(400, "Google authorization cancelled or failed")
        p.oauth_finish(state, code)
        response.delete_cookie("jarvis_oauth", path="/auth/google")
        return {"reply": "Google подключён. Можно закрыть эту вкладку."}

    def reserve(request_id, payload):
        key = "request:" + str(request_id)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if store.claim(key, {"digest": digest, "state": "running"}):
            return key, digest, None
        previous = store.get(key)
        if previous["digest"] != digest:
            raise HTTPException(409, "request_id already belongs to different input")
        if "response" in previous:
            return key, digest, previous["response"]
        raise HTTPException(
            409, "Request running or outcome uncertain; inspect remote state before a new request"
        )

    def perform(key, digest, operation, action):
        try:
            data = operation()
        except Exception:
            store.put(key, {"digest": digest, "state": "uncertain"})
            raise
        reply = data.get("reply") if isinstance(data, dict) else None
        messages = {
            "reminder": "Напоминание создано в Google Calendar.",
            "calendar.create": "Событие создано.",
            "tasks.create": "Задача создана.",
            "note": "Заметка сохранена в JARVIS_MEMORY.",
            "idea": "Идея сохранена в JARVIS_MEMORY.",
            "memory.append": "Память дополнена.",
            "gmail.send": "Письмо отправлено.",
            "jules.create": "Задача создана в Jules. План потребует отдельного одобрения в Jules.",
        }
        result = {
            "status": "completed",
            "action": action,
            "reply": reply or messages.get(action, "Готово."),
            "result": data,
        }
        store.put(key, {"digest": digest, "state": "completed", "response": result})
        return result

    @app.post("/command", dependencies=secure)
    def command(command: Command):
        # Reserve the original input: relative dates remain stable across midnight retries.
        if store.get("request:" + str(command.request_id)):
            _, _, previous = reserve(command.request_id, command.model_dump(mode="json"))
            return previous
        c = plan(command, settings.timezone)
        key, digest, previous = reserve(command.request_id, command.model_dump(mode="json"))
        if previous:
            return previous
        if c["action"] in DANGEROUS:
            if c["action"] == "jules.create":
                c.update(source=settings.jules_source, branch=settings.jules_branch)
            token = secrets.token_urlsafe(32)
            expiry = time.time() + settings.confirmation_ttl_seconds
            result = {
                "status": "confirmation_required",
                "action": c["action"],
                "reply": "Проверьте параметры и подтвердите действие.",
                "preview": c,
                "confirmation_token": token,
                "expires_at": expiry,
            }
            store.put(
                "confirm:" + token,
                {
                    "command": c,
                    "key": key,
                    "digest": digest,
                    "expires": expiry,
                    "context": confirmation_context(),
                },
            )
            store.put(key, {"digest": digest, "state": "pending", "response": result})
            return result
        return perform(key, digest, lambda: execute(p, c), c["action"])

    @app.post("/confirm", dependencies=secure)
    def confirm(body: Confirmation):
        pending = store.consume("confirm:" + body.token)
        key, digest = pending["key"], pending["digest"]
        store.put(key, {"digest": digest, "state": "running"})
        if not body.approve:
            result = {"status": "cancelled", "reply": "Действие отменено."}
            store.put(key, {"digest": digest, "state": "cancelled", "response": result})
            return result
        c = pending["command"]
        if pending["context"] != confirmation_context():
            raise HTTPException(409, "Provider account or target changed; submit a new command")
        if c["action"] == "jules.create" and (
            c["source"] != settings.jules_source or c["branch"] != settings.jules_branch
        ):
            raise HTTPException(409, "Jules configuration changed; submit a new command")
        return perform(key, digest, lambda: execute(p, c), c["action"])

    @app.get("/requests/{request_id}", dependencies=secure)
    def request_status(request_id: UUID):
        result = store.get("request:" + str(request_id))
        if not result:
            raise HTTPException(404, "Request not found")
        return result

    @app.get("/memory", dependencies=secure)
    def memory():
        return p.memory_read()

    @app.get("/memory/history", dependencies=secure)
    def history():
        return {"changes": store.history()}

    @app.post("/memory/append", dependencies=secure)
    def append(body: MemoryAppend):
        key, digest, previous = reserve(body.request_id, body.model_dump(mode="json"))
        if previous:
            return previous
        return perform(
            key,
            digest,
            lambda: p.memory_append(body.text, body.expected_revision, str(body.request_id)),
            "memory.append",
        )

    @app.get("/drive/files", dependencies=secure)
    def files(name: str = Query("", max_length=300), page_token: str | None = None):
        return p.drive_list(name, page_token)

    @app.get("/gmail/messages", dependencies=secure)
    def messages(query: str = Query("", max_length=1000), page_token: str | None = None):
        return p.gmail_list(query, page_token)

    @app.get("/gmail/messages/{message_id}", dependencies=secure)
    def message(message_id: str):
        return p.google(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + segment(message_id),
            params={"format": "full"},
        )

    @app.get("/jules/sources", dependencies=secure)
    def sources(page_token: str | None = None):
        return p.jules(
            "GET", "sources", params={"pageSize": 100, **({"pageToken": page_token} if page_token else {})}
        )

    @app.get("/jules/sessions/{session_id}", dependencies=secure)
    def session(session_id: str):
        return p.jules("GET", "sessions/" + segment(session_id))

    @app.get("/jules/sessions/{session_id}/activities", dependencies=secure)
    def activities(session_id: str, page_token: str | None = None):
        return p.jules(
            "GET",
            "sessions/" + segment(session_id) + "/activities",
            params={"pageSize": 100, **({"pageToken": page_token} if page_token else {})},
        )

    return app
