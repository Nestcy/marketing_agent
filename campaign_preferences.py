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

try:
    import psycopg
    from psycopg.rows import dict_row
    _HAS_PSYCOPG = True
except ImportError:
    _HAS_PSYCOPG = False
    dict_row = None

from typing import List, Dict, Any
import config

_in_memory_prefs: Dict[str, List[Dict[str, Any]]] = {}


def _conn():
    if not _HAS_PSYCOPG:
        raise RuntimeError("psycopg is not installed")
    return psycopg.connect(config.postgres_conn_string(), row_factory=dict_row)


def ensure_table() -> None:
    if not _HAS_PSYCOPG:
        return
    try:
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
    except Exception:
        pass


def add_preference(campaign_id: str, preference_text: str) -> None:
    if not _HAS_PSYCOPG:
        _in_memory_prefs.setdefault(campaign_id, []).append({"preference_text": preference_text})
        return
    try:
        ensure_table()
        with _conn() as conn:
            conn.execute(
                "INSERT INTO campaign_preferences (campaign_id, preference_text) VALUES (%s, %s)",
                (campaign_id, preference_text),
            )
            conn.commit()
    except Exception:
        _in_memory_prefs.setdefault(campaign_id, []).append({"preference_text": preference_text})


def get_preferences(campaign_id: str) -> List[Dict[str, Any]]:
    if not _HAS_PSYCOPG:
        return _in_memory_prefs.get(campaign_id, [])
    try:
        ensure_table()
        with _conn() as conn:
            rows = conn.execute(
                "SELECT preference_text, created_at FROM campaign_preferences "
                "WHERE campaign_id = %s ORDER BY created_at ASC",
                (campaign_id,),
            ).fetchall()
            return list(rows)
    except Exception:
        return _in_memory_prefs.get(campaign_id, [])


def get_preferences_text(campaign_id: str, limit: int = 12) -> str:
    """
    Flattened, prompt-ready block of the most recent `limit` preferences,
    oldest-of-that-set first. Capped rather than unbounded — early on this
    made no difference, but on a long-running campaign with lots of
    refine/tweak feedback, re-including the ENTIRE history in every single
    planner and per-day generation call was compounding token usage on
    every request as the campaign matured. Most recent feedback is also
    just more relevant than feedback from weeks ago that's likely already
    reflected in later drafts.
    """
    prefs = get_preferences(campaign_id)
    if not prefs:
        return ""
    recent = prefs[-limit:]
    lines = [f"- {p['preference_text']}" for p in recent]
    return "Learned preferences from the business owner (apply all of these):\n" + "\n".join(lines)

