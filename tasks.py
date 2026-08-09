# ---------------------------------------------------------
# tasks.py
#
# Daily cron: for every campaign with plan_status="approved", checks
# whether today's date has a calendar entry that hasn't been generated
# yet, generates that day's caption + image, and STOPS — leaves it at
# asset_status="awaiting_approval" (or "pending_generation" if it needs
# a reference photo). Publishing only ever happens via
# CampaignManager.approve_day(), triggered by a human, never by cron.
# ---------------------------------------------------------

import datetime
from typing import Any, Dict, List

from celery_app import celery_app
from campaign_manager import CampaignManager

_manager: CampaignManager | None = None


def _get_manager() -> CampaignManager:
    global _manager
    if _manager is None:
        _manager = CampaignManager()
    return _manager


@celery_app.task(name="tasks.generate_due_drafts")
def generate_due_drafts() -> List[Dict[str, Any]]:
    """
    Runs daily at the time set in celery_app.py's beat_schedule. For
    every approved campaign, keeps up to `auto_generate_buffer_days`
    days generated and waiting for review, counting forward from today
    (default buffer is 1 — just today). Days already generated
    (present in asset_status, regardless of current status) are
    skipped. A business can also trigger a larger batch on demand at
    any time via generate_days_ahead(), independent of this setting.
    """
    manager = _get_manager()
    today = datetime.date.today().isoformat()

    results = []
    for campaign_id in manager.list_campaign_ids():
        state = manager.get_status(campaign_id)
        if not state or state.get("plan_status") != "approved":
            continue

        calendar_dates = sorted(state.get("calendar_dates") or [])
        buffer_days = state.get("auto_generate_buffer_days") or 1
        asset_status = state.get("asset_status") or {}

        upcoming = [d for d in calendar_dates if d >= today and d not in asset_status]
        due_dates = upcoming[:buffer_days]

        for date in due_dates:
            try:
                result = manager.generate_day_asset(campaign_id, date)
                results.append({"campaign_id": campaign_id, "date": date, "result": result})

                day_plan = (result.get("calendar_plan") or {}).get(date, {})
                needs_ref = day_plan.get("needs_reference_photo", False)
                ref_images = state.get("reference_images") or {}
                has_ref = date in ref_images

                if needs_ref and not has_ref:
                    notify_reference_needed.delay(campaign_id, date)

                if result.get("status") == "awaiting_approval":
                    notify_ready_for_review.delay(campaign_id, date)
                else:
                    notify_generation_failed.delay(
                        campaign_id, date, "Generation failed (e.g. image provider error) — check logs."
                    )
            except Exception as e:
                print(f"[tasks] Failed generating {campaign_id} / {date}: {e}")

    return results


@celery_app.task(name="tasks.notify_ready_for_review")
def notify_ready_for_review(campaign_id: str, date: str) -> None:
    """
    Fires once a day's draft is generated and ready for the owner to
    review/approve. Notification hook — wire in email/Slack/in-app.
    """
    print(f"[REVIEW NEEDED] campaign={campaign_id} date={date}: draft ready for approval.")
    import campaign_events
    campaign_events.log_event(
        campaign_id, "draft_ready_for_review", payload={"date": date}, source="cron"
    )


@celery_app.task(name="tasks.notify_reference_needed")
def notify_reference_needed(campaign_id: str, date: str) -> None:
    """
    Fires when today's generated asset identified that a real reference photo is needed
    to render a personalized style image accurately.
    """
    print(f"[REFERENCE NEEDED] campaign={campaign_id} date={date}: real photo requested for personalized style asset.")
    import campaign_events
    campaign_events.log_event(
        campaign_id, "reference_photo_requested", payload={"date": date}, source="cron"
    )


@celery_app.task(name="tasks.notify_generation_failed")
def notify_generation_failed(campaign_id: str, date: str, reason: str) -> None:
    """
    Fires when a day's draft generation actually failed (e.g. an image provider error).
    """
    print(f"[GENERATION FAILED] campaign={campaign_id} date={date}: {reason}")
    import campaign_events
    campaign_events.log_event(
        campaign_id, "generation_failed", payload={"date": date, "reason": reason}, source="cron"
    )

