# ---------------------------------------------------------
# campaign_preferences.py
#
# Accumulated feedback from the business owner — every plan-refine or
# per-day tweak appends here. Read back in FULL and injected into every
# future generation prompt (plan regeneration, daily image prompt,
# daily caption prompt), so feedback compounds over the life of the
# campaign instead of being discarded after a single redo.
#
# Deliberately unscoped/global (per-campaign, not per-day-of-week or
# per-platform) — simplest version that actually works; a rules engine
# is a later problem if this proves insufficient.
# ---------------------------------------------------------

import psycopg
from psycopg.rows import dict_row
from typing import List, Dict, Any

import config


def _conn():
    return psycopg.connect(config.postgres_conn_string(), row_factory=dict_row)


def ensure_table() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_preferences (
                id BIGSERIAL PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                preference_text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_preferences_campaign_id "
            "ON campaign_preferences (campaign_id, created_at)"
        )
        conn.commit()


def add_preference(campaign_id: str, preference_text: str) -> None:
    ensure_table()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO campaign_preferences (campaign_id, preference_text) VALUES (%s, %s)",
            (campaign_id, preference_text),
        )
        conn.commit()


def get_preferences(campaign_id: str) -> List[Dict[str, Any]]:
    ensure_table()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT preference_text, created_at FROM campaign_preferences "
            "WHERE campaign_id = %s ORDER BY created_at ASC",
            (campaign_id,),
        ).fetchall()
        return list(rows)


def get_preferences_text(campaign_id: str) -> str:
    """Flattened, prompt-ready block of every preference learned so far, oldest first."""
    prefs = get_preferences(campaign_id)
    if not prefs:
        return ""
    lines = [f"- {p['preference_text']}" for p in prefs]
    return "Learned preferences from the business owner (apply all of these):\n" + "\n".join(lines)
