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
    every approved campaign, checks if today's date is in calendar_plan
    and hasn't been generated yet, and generates that one day's draft —
    ready for the owner to review, never auto-published.
    """
    manager = _get_manager()
    today = datetime.date.today().isoformat()

    results = []
    for campaign_id in manager.list_campaign_ids():
        state = manager.get_status(campaign_id)
        if not state or state.get("plan_status") != "approved":
            continue

        calendar_plan = state.get("calendar_plan") or {}
        if today not in calendar_plan:
            continue

        asset_status = state.get("asset_status") or {}
        if today in asset_status:
            continue  # already generated (or already handled) for today

        try:
            result = manager.generate_day_asset(campaign_id, today)
            results.append({"campaign_id": campaign_id, "date": today, "result": result})

            if result.get("status") == "awaiting_approval":
                notify_ready_for_review.delay(campaign_id, today)
            else:
                request_reference_image.delay(
                    campaign_id, today, "Today's post needs a reference photo before it can be generated."
                )
        except Exception as e:
            print(f"[tasks] Failed generating {campaign_id} / {today}: {e}")

    return results


@celery_app.task(name="tasks.notify_ready_for_review")
def notify_ready_for_review(campaign_id: str, date: str) -> None:
    """
    Fires once a day's draft is generated and ready for the owner to
    review/approve. Notification hook — wire in email/Slack/in-app
    once you've picked a provider.
    """
    # TODO: replace with a real notification.
    print(f"[REVIEW NEEDED] campaign={campaign_id} date={date}: draft ready for approval.")
    import campaign_events
    campaign_events.log_event(
        campaign_id, "draft_ready_for_review", payload={"date": date}, source="cron"
    )


@celery_app.task(name="tasks.request_reference_image")
def request_reference_image(campaign_id: str, date: str, reason: str) -> None:
    """
    Fires when a day's idea needs a reference photo that hasn't been
    supplied. Notification hook — same TODO as above.
    """
    print(f"[ACTION NEEDED] campaign={campaign_id} date={date}: {reason}")
    import campaign_events
    campaign_events.log_event(
        campaign_id, "reference_photo_requested", payload={"date": date, "reason": reason}, source="cron"
    )
