"""Generate local keys without printing them or overwriting an existing .env."""

import secrets
from pathlib import Path

from cryptography.fernet import Fernet

root = Path(__file__).resolve().parents[1]
template = (root / ".env.example").read_text(encoding="utf-8")
template = template.replace("JARVIS_API_KEY=\n", "JARVIS_API_KEY=" + secrets.token_urlsafe(48) + "\n")
template = template.replace(
    "TOKEN_ENCRYPTION_KEY=\n", "TOKEN_ENCRYPTION_KEY=" + Fernet.generate_key().decode() + "\n"
)
with (root / ".env").open("x", encoding="utf-8") as output:
    output.write(template)
print("Created .env with local keys. Add Google/Jules configuration; do not commit this file.")
