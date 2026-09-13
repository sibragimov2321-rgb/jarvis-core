import json
import sqlite3
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet
from fastapi import HTTPException


class Store:
    """Encrypted durable payloads; atomic claims prevent duplicate execution after retries/crashes."""

    def __init__(self, settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = settings.data_dir / "jarvis.db"
        self.cipher = Fernet(settings.token_encryption_key.get_secret_value().encode())
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value BLOB)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def encode(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def decode(self, value):
        return json.loads(self.cipher.decrypt(value))

    def get(self, key):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return self.decode(row[0]) if row else None

    def put(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, self.encode(value)))

    def claim(self, key, value):
        with self.connect() as db:
            return (
                db.execute("INSERT OR IGNORE INTO kv VALUES (?,?)", (key, self.encode(value))).rowcount == 1
            )

    def consume(self, key):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            if not row:
                raise HTTPException(409, "Token missing or already used")
            value = self.decode(row[0])
            if value["expires"] < time.time():
                raise HTTPException(410, "Token expired")
            db.execute("DELETE FROM kv WHERE key=?", (key,))
            return value

    def history(self):
        with self.connect() as db:
            rows = db.execute("SELECT value FROM kv WHERE key LIKE 'memory:%' ORDER BY key DESC LIMIT 100")
            return [self.decode(row[0]) for row in rows]
