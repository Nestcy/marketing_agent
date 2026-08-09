# Marketing Agent (v3 — 3D Organic Content Calendar & Daily Asset Generation)

Autonomous organic content platform: researches a business (Tavily +
Firecrawl), drafts a focused **3-day (3d) content calendar outline** to respect
model rate limits, and — once approved — follows through with daily cron jobs or tasks
that generate captions and image prompts for each day using image models. Where personalized
style images require real photos, it prompts the business owner for reference images, while
allowing owners to refine or tweak images whenever they wish.

## The two approval gates & image tweaking

1. **Plan gate** — `start_campaign()` produces a LIGHTWEIGHT 3-day strategy
   outline (content pillars, tone, platform mix). One small LLM call for a 3-day (3d) scope,
   preventing model rate limit exhaustion. Owner either `approve_plan()`s
   it, or `refine_plan()`s it with feedback.
2. **Day gate & Image Tweaking** — once approved, each day's asset draft (idea, platform,
   reference-need, caption, and image prompt) gets generated (by daily cron or on-demand)
   ONLY when that day is actually about to be reviewed.
   - If a personalized style image requires authentic business photos, `needs_reference_photo`
     is flagged and `notify_reference_needed` dispatches a request to the business owner.
   - Owners can `tweak_image()` specifically to refine the visual style using custom feedback.
   - Owner `approve_day()`s the final asset once satisfied.

**Feedback compounds.** Every `refine_plan`/`tweak_day`/`tweak_image` call appends to
`campaign_preferences` (Postgres), and that full accumulated list gets
injected into every future generation prompt — plan regeneration AND
every day's caption/image prompt. Say "less product-focused" or "more vibrant lighting"
once, and future generations already reflect it.

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
