import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Use the READ-ONLY database user's URL here (see db/readonly_user.sql). The code also
# opens the connection read-only and checks every statement, but the database grant is the
# layer that doesn't depend on this code being right.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'demo.db'}")

MAX_ROWS = int(os.getenv("MAX_ROWS", "100"))
QUERY_TIMEOUT_SECONDS = float(os.getenv("QUERY_TIMEOUT_SECONDS", "5"))
# Most connections one process may hold open. db/readonly_user.sql allows 20 per database user, so
# (processes x DB_POOL_SIZE) must stay under that, or MySQL answers "max_user_connections".
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# Columns the model never sees and queries may never mention (personal data).
BLOCKED_IDENTIFIERS = tuple(
    name.strip().lower() for name in os.getenv("BLOCKED_IDENTIFIERS", "email").split(",") if name.strip()
)
