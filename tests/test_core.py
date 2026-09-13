import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient

from jarvis.app import create_app
from jarvis.commands import Command, plan
from jarvis.config import Settings
from jarvis.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        jarvis_api_key="a" * 40,
        token_encryption_key=Fernet.generate_key().decode(),
        data_dir=tmp_path,
        google_client_id="client",
        google_client_secret="secret",
        jarvis_memory_id="doc",
    )


class Fake:
    def __init__(self):
        self.calls = []

    def calendar_create(self, *args):
        self.calls.append(args)
        return {"id": "event"}

    def gmail_send(self, *args):
        self.calls.append(args)
        return {"id": "sent"}

    def chat(self, text):
        return {"reply": text}


@pytest.fixture
def setup(settings):
    fake = Fake()
    app = create_app(settings, fake)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + "a" * 40
        yield client, fake, app.state.store


def test_auth_is_required(setup):
    client, _, _ = setup
    client.headers.clear()
    for path in [
        "/memory",
        "/memory/history",
        "/drive/files",
        "/gmail/messages",
        "/jules/sources",
        "/status",
    ]:
        assert client.get(path).status_code == 401
    assert client.post("/command", json={"request_id": str(uuid4()), "text": "hello"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_russian_reminder_and_retry(setup):
    client, fake, _ = setup
    body = {"request_id": str(uuid4()), "text": "завтра в 11 напомни проверить trading bot"}
    first = client.post("/command", json=body)
    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert fake.calls[0][0] == "проверить trading bot"
    assert "T11:00:00+03:00" in fake.calls[0][1]
    assert client.post("/command", json=body).json() == first.json()
    assert len(fake.calls) == 1
    body["text"] = "другая команда"
    assert client.post("/command", json=body).status_code == 409


def mail():
    return {
        "request_id": str(uuid4()),
        "action": "gmail.send",
        "to": "a@example.com",
        "subject": "Review",
        "body": "Exact message",
    }


def test_confirmation_exact_payload_and_replay(setup):
    client, fake, _ = setup
    body = mail()
    preview = client.post("/command", json=body).json()
    assert preview["status"] == "confirmation_required"
    assert preview["preview"]["body"] == "Exact message"
    assert fake.calls == []
    confirmation = {"token": preview["confirmation_token"], "approve": True}
    assert client.post("/confirm", json={**confirmation, "body": "hijack"}).status_code == 422
    assert client.post("/confirm", json=confirmation).status_code == 200
    assert client.post("/confirm", json=confirmation).status_code == 409
    assert client.post("/command", json=body).json()["status"] == "completed"
    assert fake.calls == [("a@example.com", "Review", "Exact message")]


def test_confirmation_cancel_and_expiry(setup):
    client, fake, store = setup
    token = client.post("/command", json=mail()).json()["confirmation_token"]
    assert client.post("/confirm", json={"token": token, "approve": False}).json()["status"] == "cancelled"
    token = client.post("/command", json=mail()).json()["confirmation_token"]
    entry = store.get("confirm:" + token)
    entry["expires"] = time.time() - 1
    store.put("confirm:" + token, entry)
    assert client.post("/confirm", json={"token": token, "approve": True}).status_code == 410
    assert fake.calls == []


@pytest.mark.parametrize("text", ["завтра в 25 напомни тест", "напомни когда-нибудь", "deploy production"])
def test_ambiguous_and_unsafe_text(setup, text):
    client, fake, _ = setup
    assert client.post("/command", json={"request_id": str(uuid4()), "text": text}).status_code == 422
    assert fake.calls == []


def test_explicit_naive_time_rejected(setup):
    client, _, _ = setup
    response = client.post(
        "/command",
        json={
            "request_id": str(uuid4()),
            "action": "calendar.create",
            "title": "test",
            "when": "2099-01-01T11:00:00",
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "text,action",
    [
        ("идея: бот", "idea"),
        ("заметка: тест", "note"),
        ("задача: проверить", "tasks.create"),
        ("программисту: исправь тест", "jules.create"),
        ("календарь", "calendar.list"),
        ("drive: MEMORY", "drive.list"),
        ("почта", "gmail.list"),
        ("Как организовать день?", "chat"),
    ],
)
def test_routing(text, action):
    assert plan(Command(request_id=uuid4(), text=text), "Europe/Moscow")["action"] == action


def test_relative_date_year_rollover():
    result = plan(
        Command(request_id=uuid4(), text="завтра в 11 напомни тест"),
        "Europe/Moscow",
        datetime(2026, 12, 31, 15, tzinfo=ZoneInfo("Europe/Moscow")),
    )
    assert result["when"] == "2027-01-01T11:00:00+03:00"


def test_atomic_claim_and_encryption(settings):
    store = Store(settings)
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda _: store.claim("once", {"secret": "private-memory-text"}), range(20)))
    assert sum(claims) == 1
    assert b"private-memory-text" not in store.path.read_bytes()
    assert Store(settings).get("once")["secret"] == "private-memory-text"


def test_provider_failure_not_retried(setup):
    client, fake, _ = setup

    def fail(*args):
        fake.calls.append(args)
        raise HTTPException(502, "timeout")

    fake.calendar_create = fail
    body = {"request_id": str(uuid4()), "text": "завтра в 11 напомни тест"}
    assert client.post("/command", json=body).status_code == 502
    assert client.post("/command", json=body).status_code == 409
    assert len(fake.calls) == 1


def test_unknown_action_and_header_injection(setup):
    client, _, _ = setup
    assert client.post("/command", json={"request_id": str(uuid4()), "action": "shell"}).status_code == 422
    assert (
        client.post("/command", json={**mail(), "subject": "a\r\nBcc: victim@example.com"}).status_code == 422
    )
