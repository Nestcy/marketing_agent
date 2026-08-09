# Marketing Agent (v3 — organic content calendar, two approval gates)

Autonomous organic content platform: researches a business (Tavily +
Firecrawl), drafts a full day-by-day content calendar (30 or 90 days),
and — once approved — generates each day's image + caption one at a
time, publishing to Facebook/Instagram only after explicit owner
approval of that specific day. No ad budget, no ad-platform spend —
this is organic crossposting.

## The two approval gates

1. **Plan gate** — `start_campaign()` produces a LIGHTWEIGHT strategy
   outline (content pillars, tone, platform mix) — NOT a full day-by-day
   calendar. One small LLM call regardless of a 30 or 90 day timeframe;
   this is the main token-savings change. Owner either `approve_plan()`s
   it, or `refine_plan()`s it with feedback (regenerates the outline —
   still one small call).
2. **Day gate** — once approved, each day's draft (idea, platform,
   reference-need, caption, and image, ALL decided in one combined call)
   gets generated (by cron or on-demand) ONLY when that day is actually
   about to be reviewed — never speculatively for future days — and sits
   at `awaiting_approval`. Owner either `approve_day()`s it (finalizes
   it — no publishing anywhere yet, that's a future Lovable-side
   feature) or `tweak_day()`s it with feedback (regenerates just that
   day, still requires approval after).

**Feedback compounds.** Every `refine_plan`/`tweak_day` call appends to
`campaign_preferences` (Postgres), and that full accumulated list gets
injected into every future generation prompt — plan regeneration AND
every day's caption/image prompt. Say "less product-focused" once on
day 3, and day 10 already reflects it.

## What runs where

- **`main.py`** — FastAPI app (`web` process)
- **`celery_app.py` + `tasks.py`** — daily cron (`worker` + `beat`)
  that generates (not publishes) each day's due draft
- **`marketing_engine.py`** — `build_graph()` is JUST the planning
  graph (research → calendar draft → stop). `generate_daily_asset()`
  is a standalone function, called once per day, NOT chained in a graph.
- **`campaign_manager.py`** — the two-gate state machine on top of the
  above, Postgres-backed

## Deploying to Railway

Three services from this repo, each on a different `Procfile` process
type (`web`, `worker`, `beat`). Attach Postgres + Redis plugins, set
the vars from `.env.example` on **all three** services.

## Research — direct REST, no MCP

`research_clients.py` calls Tavily's and Firecrawl's REST APIs
directly with plain API keys (`TAVILY_API_KEY`, `FIRECRAWL_API_KEY`) —
no MCP session/protocol involved.

## Free vs. paid image tier

`user_plan: "free"` (default) → Gemini free tier. `user_plan: "paid"`
→ DALL-E 3.

## Organic publishing

`publisher_clients.py` posts a single approved image+caption to
Facebook (Graph API photo post) or Instagram (Graph API media
container flow). TikTok posting is a stub — requires app review before
it can be wired in for real.

## Local dev

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
# separately:
celery -A celery_app worker --loglevel=info
celery -A celery_app beat --loglevel=info
```

Smoke test without any real API keys:
```bash
python test_image_routing.py
```
