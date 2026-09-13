import re
from datetime import date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

Action = Literal[
    "chat",
    "idea",
    "note",
    "reminder",
    "calendar.create",
    "calendar.list",
    "calendar.delete",
    "tasks.create",
    "tasks.list",
    "tasks.delete",
    "drive.list",
    "gmail.list",
    "gmail.send",
    "gmail.trash",
    "jules.create",
]
DANGEROUS = {"calendar.delete", "tasks.delete", "gmail.send", "gmail.trash", "jules.create"}


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    text: str = Field("", max_length=10000)
    action: Action | None = None
    title: str = Field("", max_length=1000)
    when: datetime | None = None
    due: date | None = None
    resource_id: str = Field("", max_length=300, pattern=r"^[a-zA-Z0-9_@.\-]*$")
    to: str = Field("", max_length=320)
    subject: str = Field("", max_length=998)
    body: str = Field("", max_length=50000)


def plan(command, timezone, now=None):
    c = command.model_dump(mode="json")
    text = command.text.strip()
    action = command.action
    if not action:
        # Deliberately conservative grammar; no LLM can call tools or authorize actions.
        match = re.fullmatch(
            r"(сегодня|завтра|послезавтра)\s+в\s+(\d{1,2})(?::(\d{2}))?\s+напомни\s+(.+)", text, re.IGNORECASE
        )
        if match:
            day, hour, minute, title = match.groups()
            if int(hour) > 23 or int(minute or 0) > 59:
                raise HTTPException(422, "Укажите корректное время, например завтра в 11:30 напомни ...")
            now = now or datetime.now(ZoneInfo(timezone))
            when = (now + timedelta(days={"сегодня": 0, "завтра": 1, "послезавтра": 2}[day.lower()])).replace(
                hour=int(hour), minute=int(minute or 0), second=0, microsecond=0
            )
            c.update(title=title, when=when.isoformat())
            action = "reminder"
        elif re.match(r"^(идея|заметка|запиши|задача|программисту)\s*:", text, re.IGNORECASE):
            prefix, content = text.split(":", 1)
            action = {
                "идея": "idea",
                "заметка": "note",
                "запиши": "note",
                "задача": "tasks.create",
                "программисту": "jules.create",
            }[prefix.lower().strip()]
            c.update(title=content.strip(), text=content.strip())
        elif text.lower() in {"календарь", "покажи календарь"}:
            action = "calendar.list"
        elif text.lower() in {"задачи", "покажи задачи"}:
            action = "tasks.list"
        elif text.lower().startswith("drive:"):
            action, c["text"] = "drive.list", text.split(":", 1)[1].strip()
        elif text.lower() in {"почта", "gmail", "покажи почту"}:
            action = "gmail.list"
        elif re.search(
            r"напомни|отправь|удали|разверни|деплой|deploy|production|создай.*(событие|письмо)",
            text,
            re.IGNORECASE,
        ):
            raise HTTPException(
                422,
                "Нужны точные параметры. Используйте action в /docs; "
                "напоминание: «завтра в 11 напомни проверить trading bot». "
                "Production и произвольные команды не поддерживаются.",
            )
        else:
            action = "chat"
    c["action"] = action
    if action in {"reminder", "calendar.create"}:
        if not c["title"] or not c["when"]:
            raise HTTPException(422, "title and timezone-aware when are required")
        when = datetime.fromisoformat(c["when"])
        if when.tzinfo is None or when <= (now or datetime.now(ZoneInfo(timezone))):
            raise HTTPException(422, "when must be in the future and include UTC offset")
    if action == "tasks.create" and not c["title"]:
        raise HTTPException(422, "title is required")
    if action in {"idea", "note", "chat", "jules.create"} and not c["text"].strip():
        raise HTTPException(422, "text is required")
    if action in {"calendar.delete", "tasks.delete", "gmail.trash"} and not c["resource_id"]:
        raise HTTPException(422, "resource_id is required")
    if action == "gmail.send":
        if (
            not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", c["to"])
            or not c["subject"]
            or not c["body"]
        ):
            raise HTTPException(422, "Provide one email address, subject and body")
        if any(x in c["to"] + c["subject"] for x in "\r\n"):
            raise HTTPException(422, "Email headers cannot contain newlines")
    return c


def execute(p, c):
    action = c["action"]
    if action == "chat":
        return p.chat(c["text"])
    if action in {"idea", "note"}:
        before = p.memory_read()
        label = "Идея" if action == "idea" else "Заметка"
        return p.memory_append(f"[{label}] {c['text']}", before["revision"], c["request_id"])
    if action in {"calendar.create", "reminder"}:
        return p.calendar_create(c["title"], c["when"], c["request_id"])
    if action == "calendar.list":
        return p.calendar_list()
    if action == "tasks.create":
        return p.task_create(c["title"], c["due"])
    if action == "tasks.list":
        return p.google("GET", p.tasks_url(), params={"maxResults": 100})
    if action == "drive.list":
        return p.drive_list(c["text"])
    if action == "gmail.list":
        return p.gmail_list(c["text"] if c["text"] not in {"почта", "gmail", "покажи почту"} else "")
    if action == "gmail.send":
        return p.gmail_send(c["to"], c["subject"], c["body"])
    if action == "gmail.trash":
        return p.google(
            "POST", "https://gmail.googleapis.com/gmail/v1/users/me/messages/" + c["resource_id"] + "/trash"
        )
    if action == "calendar.delete":
        return p.google("DELETE", p.calendar_url() + "/" + c["resource_id"], params={"sendUpdates": "none"})
    if action == "tasks.delete":
        return p.google("DELETE", p.tasks_url() + "/" + c["resource_id"])
    if action == "jules.create":
        return p.jules_create(c["text"])
    raise HTTPException(422, "Unsupported action")
