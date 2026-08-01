# ---------------------------------------------------------
# campaign_registry.py
#
# LangGraph's PostgresSaver checkpointer (used by campaign_manager.py)
# stores state per thread_id, but doesn't give you a simple "list every
# thread_id that exists" query. This is a tiny separate table, in the
# same Postgres database, that just tracks which campaign_ids exist —
# so tasks.py's daily Celery task knows which campaigns to check for
# assets due to publish "today".
# ---------------------------------------------------------

import psycopg

import config


def _conn():
    return psycopg.connect(config.postgres_conn_string())


def ensure_table() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_registry (
                campaign_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        conn.commit()


def register(campaign_id: str) -> None:
    ensure_table()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO campaign_registry (campaign_id) VALUES (%s) "
            "ON CONFLICT (campaign_id) DO NOTHING",
            (campaign_id,),
        )
        conn.commit()


def list_ids() -> list[str]:
    ensure_table()
    with _conn() as conn:
        rows = conn.execute("SELECT campaign_id FROM campaign_registry").fetchall()
        return [r[0] for r in rows]
