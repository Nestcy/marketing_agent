# ---------------------------------------------------------
# celery_app.py
#
# Celery app + daily beat schedule. This is what turns the
# week_1..week_4 plan (via calendar_scheduling_node's publish_schedule)
# into an actual "publish one new asset per day" cron behavior.
#
# On Railway, run this alongside the FastAPI web process as two (or
# three) separate services from the same repo — see the Procfile:
#   - web:    FastAPI app (main.py)
#   - worker: celery -A celery_app worker
#   - beat:   celery -A celery_app beat
#
# All three need REDIS_URL and POSTGRES_CONN_STRING set. Railway's
# Redis plugin can be attached to this service and it'll expose
# REDIS_URL automatically.
# ---------------------------------------------------------

from celery import Celery
from celery.schedules import crontab

import config

celery_app = Celery(
    "marketing_engine",
    broker=config.redis_url(),
    backend=config.redis_url(),
    include=["tasks"],
)

celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "publish-due-assets-daily": {
        "task": "tasks.publish_due_assets",
        "schedule": crontab(hour=9, minute=0),  # 09:00 UTC daily — adjust as needed
    },
}
