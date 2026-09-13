import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.config import Settings
from jarvis.providers import SCOPES, Providers
from jarvis.store import Store


@pytest.fixture
def rig(tmp_path):
    s = Settings(
        _env_file=None,
        jarvis_api_key="k" * 40,
        token_encryption_key=Fernet.generate_key().decode(),
        data_dir=tmp_path,
        google_client_id="client",
        google_client_secret="private-client-secret",
        jarvis_memory_id="memory-doc",
        jules_api_key="private-jules-key",
        jules_source="sources/test",
    )
    store = Store(s)
    store.put(
        "google_token",
        {
            "access_token": "private-access",
            "refresh_token": "private-refresh",
            "expires_at": time.time() + 3600,
        },
    )
    return s, store


def provider(rig, handler):
    return Providers(*rig, httpx.Client(transport=httpx.MockTransport(handler)))


def document(revision="r1", tabs=1):
    return {
        "revisionId": revision,
        "tabs": [
            {
                "tabProperties": {"tabId": f"t{i}"},
                "documentTab": {
                    "body": {
                        "content": [{"paragraph": {"elements": [{"textRun": {"content": "existing\n"}}]}}]
                    }
                },
            }
            for i in range(tabs)
        ],
    }


def test_memory_append_revision_and_history(rig):
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=document())
        body = json.loads(request.content)
        assert body["writeControl"] == {"requiredRevisionId": "r1"}
        assert body["requests"] == [
            {"insertText": {"endOfSegmentLocation": {"tabId": "t0"}, "text": "\nnew\n"}}
        ]
        return httpx.Response(200, json={"writeControl": {"requiredRevisionId": "r2"}})

    p = provider(rig, handler)
    p.memory_append("new", "r1", "request")
    assert len(calls) == 2
    history = rig[1].history()
    assert history[0]["before"]["text"] == "existing\n"
    assert history[0]["status"] == "applied"


def test_memory_conflict_does_not_write(rig):
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json=document("r2"))

    with pytest.raises(HTTPException) as error:
        provider(rig, handler).memory_append("new", "r1", "request")
    assert error.value.status_code == 409
    assert calls == ["GET"]


def test_remote_memory_race_preserves_pending_history(rig):
    def handler(request):
        return httpx.Response(200, json=document()) if request.method == "GET" else httpx.Response(400)

    with pytest.raises(HTTPException):
        provider(rig, handler).memory_append("new", "r1", "request")
    assert rig[1].history()[0]["status"] == "unknown_or_failed"


def test_multitab_requires_explicit_selection(rig):
    p = provider(rig, lambda _: httpx.Response(200, json=document(tabs=2)))
    with pytest.raises(HTTPException):
        p.memory_read()
    rig[0].jarvis_memory_tab_id = "t1"
    assert p.memory_read()["tab_id"] == "t1"


def test_oauth_pkce_and_single_use_state(rig):
    seen = []

    def handler(request):
        form = parse_qs(request.content.decode())
        seen.append(form)
        assert form["grant_type"] == ["authorization_code"]
        return httpx.Response(
            200,
            json={
                "access_token": "new",
                "refresh_token": "new-refresh",
                "scope": " ".join(SCOPES),
                "expires_in": 3600,
            },
        )

    p = provider(rig, handler)
    query = parse_qs(urlparse(p.oauth_start()).query)
    state = query["state"][0]
    verifier = rig[1].get("oauth:" + state)["verifier"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert query["code_challenge"] == [challenge]
    p.oauth_finish(state, "code")
    assert seen[0]["code_verifier"] == [verifier]
    assert rig[1].get("google_token")["connection_id"]
    with pytest.raises(HTTPException):
        p.oauth_finish(state, "code")
    assert len(seen) == 1


def test_oauth_browser_binding(rig):
    p = provider(rig, lambda _: httpx.Response(200, json={}))
    with TestClient(create_app(rig[0], p)) as client:
        assert client.get("/auth/google/callback?state=bogus&code=code").status_code == 400
        client.headers["Authorization"] = "Bearer " + "k" * 40
        response = client.post("/auth/google/start")
        state = parse_qs(urlparse(response.json()["authorization_url"]).query)["state"][0]
        assert client.cookies["jarvis_oauth"] == state
        assert response.headers["Cache-Control"] == "no-store"


def test_token_refresh_and_secret_error_redaction(rig):
    old = rig[1].get("google_token")
    old["expires_at"] = 0
    rig[1].put("google_token", old)
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer refreshed"
        return httpx.Response(403, json={"error": "private-client-secret"})

    with pytest.raises(HTTPException) as error:
        provider(rig, handler).calendar_list()
    assert "private" not in str(error.value.detail)
    assert rig[1].get("google_token")["refresh_token"] == "private-refresh"
    assert len(calls) == 2


def test_provider_payload_contracts(rig):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "created"})

    p = provider(rig, handler)
    p.calendar_create("title", "2099-01-01T11:00:00+03:00", str(uuid4()))
    calendar = json.loads(seen[-1].content)
    assert calendar["end"]["dateTime"] == "2099-01-01T11:15:00+03:00"
    assert calendar["reminders"]["overrides"][0]["minutes"] == 0
    assert "attendees" not in calendar
    p.task_create("task", "2099-01-02")
    assert json.loads(seen[-1].content)["due"] == "2099-01-02T00:00:00Z"
    p.gmail_send("a@example.com", "hello", "body")
    raw = base64.urlsafe_b64decode(json.loads(seen[-1].content)["raw"])
    assert b"To: a@example.com" in raw and b"body" in raw
    p.jules_create("Implement tests")
    jules = json.loads(seen[-1].content)
    assert seen[-1].url.host == "jules.googleapis.com"
    assert jules["requirePlanApproval"] is True
    assert "automationMode" not in jules
    assert jules["sourceContext"]["source"] == "sources/test"


def test_unconfigured_chat_honest(rig):
    p = provider(rig, lambda _: pytest.fail("Must not make API call"))
    assert "Gemini API" in p.chat("hi")["reply"]
