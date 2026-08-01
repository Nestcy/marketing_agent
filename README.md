# Marketing Agent

Autonomous marketing engine: researches a business (Brave Search +
Firecrawl), plans a 4-week campaign, writes ad copy, generates images
(Gemini free tier by default, DALL-E 3 / Stable Diffusion on paid),
builds a daily publish calendar, and publishes to Facebook/TikTok/Google
Ads — with a daily Celery cron rolling assets out one per day instead of
all at once.

**Video generation was removed.** This platform is image-only. See
git history if you need to reference the old `video_clients.py`.

## What actually runs where

- **`main.py`** — FastAPI app (the `web` process)
- **`celery_app.py` + `tasks.py`** — daily cron (`worker` + `beat` processes)
  that publishes assets on their scheduled day
- **`marketing_engine.py`** — the LangGraph pipeline: research → plan →
  ideate → generate images → schedule → initial publish
- **`campaign_manager.py`** — persistent, multi-campaign, reroutable
  wrapper around the graph (Postgres-backed)

## Deploying to Railway

This repo's `Procfile` defines three process types. On Railway, create
**three services from the same repo**, each pointing at a different
Procfile process type (`web`, `worker`, `beat`) — Railway lets you pick
the start command per service, or you can just set each service's start
command explicitly to the matching line from the `Procfile`.

1. Attach a **Postgres** plugin (for `POSTGRES_CONN_STRING` — campaign
   state + the `campaign_registry` table)
2. Attach a **Redis** plugin (for `REDIS_URL` — Celery broker/backend)
3. Set the environment variables from `.env.example` on all three
   services (they need to share the same Postgres/Redis/API keys)
4. Deploy `web`, `worker`, and `beat` as separate services

## MCP servers — you'll need to fill in real endpoints

`mcp_clients.py` has placeholder URLs and tool names for Brave Search,
Firecrawl, and your chosen Calendar MCP server. Brave/Firecrawl's
defaults are reasonable guesses; **the calendar one is generic** — swap
in your actual provider's endpoint and check its real tool
name/argument shape via an MCP inspector before relying on it, since
this varies a lot between Google Calendar MCP servers, Cal.com MCP,
Nylas MCP, etc.

## Free vs. paid image tier

`user_plan: "free"` (default) routes every image through Gemini's free
tier (`gemini_free` in `image_clients.py`). `user_plan: "paid"` routes
through DALL-E 3 / Stable Diffusion based on the asset type, same
heuristic as before.

## Reference-photo gating

If the planner describes an asset as needing a real photo (phrases like
"reference photo" or "needs photo" — see `_REFERENCE_NEEDED_PHRASES` in
`marketing_engine.py`), that asset is **not** auto-generated. It's added
to `pending_reference_requests`, and the business uploads a real photo
via `POST /api/campaign/{campaign_id}/reference-image`, which triggers
image-to-image generation for just that one asset without replanning
the rest of the campaign.

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in at least GEMINI_API_KEY, GROQ_API_KEY, POSTGRES_CONN_STRING, REDIS_URL
uvicorn main:app --reload
# separately:
celery -A celery_app worker --loglevel=info
celery -A celery_app beat --loglevel=info
```

Quick sanity checks without any real API keys:
```bash
python test_image_routing.py
```
