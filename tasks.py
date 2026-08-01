# ---------------------------------------------------------
# tasks.py
#
# Celery tasks. publish_due_assets is the daily cron job (scheduled via
# celery_app.py's beat_schedule) that actually implements "publish one
# scheduled asset per day" — it does NOT regenerate or replan anything,
# it just checks each active campaign's publish_schedule (built by
# calendar_scheduling_node) for asset_ids due "today" and pushes just
# those to the ad platforms.
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


@celery_app.task(name="tasks.publish_due_assets")
def publish_due_assets() -> List[Dict[str, Any]]:
    """
    Runs daily at the time set in celery_app.py's beat_schedule. For
    every campaign that's ever been started (campaign_registry), checks
    publish_schedule for asset_ids whose scheduled date is today, and
    publishes just those — so a 4-week plan actually rolls out one
    asset at a time instead of dumping everything live on day one.
    """
    manager = _get_manager()
    today = datetime.date.today().isoformat()

    results = []
    for campaign_id in manager.list_campaign_ids():
        state = manager.get_status(campaign_id)
        if not state:
            continue

        schedule = state.get("publish_schedule") or {}
        due_today = [asset_id for asset_id, date in schedule.items() if date == today]
        if not due_today:
            continue

        results.append(_publish_due_assets_for_campaign(campaign_id, state, due_today))

    return results


def _publish_due_assets_for_campaign(
    campaign_id: str, state: Dict[str, Any], asset_ids: List[str]
) -> Dict[str, Any]:
    from publisher_clients import publish_to_all_platforms

    all_images = state.get("generated_images") or {}
    due_images = {aid: info for aid, info in all_images.items() if aid in asset_ids}

    still_pending = [aid for aid in asset_ids if aid not in due_images]
    for asset_id in still_pending:
        request_reference_image.delay(
            campaign_id, asset_id, "Asset is due to publish today but has no generated image yet."
        )

    if not due_images:
        return {"campaign_id": campaign_id, "published": [], "note": "all due assets still pending"}

    status = publish_to_all_platforms(
        campaign_name=f"{state.get('campaign_goal', 'Campaign')} - {datetime.date.today().isoformat()}",
        budget_allocations=state.get("budget_allocations", {}),
        images=due_images,
    )
    return {"campaign_id": campaign_id, "published": list(due_images.keys()), "status": status}


@celery_app.task(name="tasks.request_reference_image")
def request_reference_image(campaign_id: str, asset_id: str, reason: str) -> None:
    """
    Fires when an asset needs a business-supplied reference photo (see
    _needs_reference_photo in marketing_engine.py) or is due to publish
    but still has no image. This is a notification hook — wire in your
    actual notification provider (email/Slack/in-app) here. Once the
    business responds, hit the API's reference-image upload endpoint,
    which calls CampaignManager.submit_reference_image().
    """
    # TODO: replace with a real notification (email via SES/Postmark,
    # Slack webhook, or an in-app notification row) once you've picked one.
    print(f"[ACTION NEEDED] campaign={campaign_id} asset={asset_id}: {reason}")
