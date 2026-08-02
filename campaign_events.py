# ---------------------------------------------------------
# campaign_events.py
#
# A persistent, append-only event log — separate from LangGraph's
# checkpointed state and separate from chat history. Every meaningful
# thing that happens to a campaign writes a row here, whether it was
# triggered by a person chatting or by the daily Celery cron. This is
# what the Dashboard Hub's timeline view reads from.
#
# Kept intentionally simple (one flat table, JSON payload) rather than
# a rigid schema per event type, since event shapes will keep growing
# as you add more chat-triggerable actions.
# ---------------------------------------------------------

import json
import datetime
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row

import config


def _conn():
    return psycopg.connect(config.postgres_conn_string(), row_factory=dict_row)


def ensure_table() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_events (
                id BIGSERIAL PRIMARY KEY,
                campaign_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb,
                source TEXT NOT NULL DEFAULT 'system',
                created_at TIMESTAMPTZ DEFAULT now()
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_campaign_events_campaign_id "
            "ON campaign_events (campaign_id, created_at DESC)"
        )
        conn.commit()


# ---------------------------------------------------------
# Known event_type values (not enforced, just documented — keep new
# event types consistent with this list where they fit):
#
#   "campaign_started"        payload: {business_context, goal, budget, ...}
#   "plan_generated"          payload: {campaign_plan, budget_allocations}
#   "images_generated"        payload: {asset_ids: [...]}
#   "reference_photo_requested"  payload: {asset_id, reason}
#   "reference_photo_uploaded"   payload: {asset_id}
#   "campaign_rerouted"        payload: {updates: {...}}
#   "asset_published"          payload: {asset_id, platform, status}
#   "cron_publish_run"         payload: {published: [asset_ids], date}
#   "chat_message"             payload: {role, content}
#
# source is "chat" | "cron" | "api" | "system"
# ---------------------------------------------------------

def log_event(
    campaign_id: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    source: str = "system",
) -> None:
    ensure_table()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO campaign_events (campaign_id, event_type, payload, source) "
            "VALUES (%s, %s, %s, %s)",
            (campaign_id, event_type, json.dumps(payload or {}), source),
        )
        conn.commit()


def get_timeline(campaign_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Most recent events first — what the Hub's per-campaign timeline renders."""
    ensure_table()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, campaign_id, event_type, payload, source, created_at "
            "FROM campaign_events WHERE campaign_id = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (campaign_id, limit),
        ).fetchall()
        return [
            {**r, "created_at": r["created_at"].isoformat()}
            for r in rows
        ]


def get_all_recent_events(limit: int = 100) -> List[Dict[str, Any]]:
    """Cross-campaign feed — for a Hub landing page showing activity across everything."""
    ensure_table()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, campaign_id, event_type, payload, source, created_at "
            "FROM campaign_events ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [
            {**r, "created_at": r["created_at"].isoformat()}
            for r in rows
        ]
