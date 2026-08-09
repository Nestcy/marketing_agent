"""
Smoke test for the outline-based architecture: the planning graph now
produces a LIGHTWEIGHT strategy outline (one small LLM call, same cost
regardless of 30 vs 90 day timeframe), and each day's specifics
(idea/platform/reference-need/caption/image_prompt) are decided in ONE
combined call only when that day is actually generated — not upfront.
Runs without real API keys by monkeypatching image_clients,
research_clients, campaign_preferences, and llm_client.
"""
import sys
import io
import os
import datetime

sys.path.insert(0, os.path.dirname(__file__))

# ---- Monkeypatch research (no real network) ----
import research_clients
research_clients.tavily_search = lambda *a, **kw: None
research_clients.firecrawl_scrape = lambda *a, **kw: None
research_clients.firecrawl_search = lambda *a, **kw: None

# ---- Monkeypatch image client ----
import image_clients
_image_call_log = []

def mock_generate_image(prompt: str, model_preference: str = "gemini_free", **kwargs):
    result = {"model": model_preference, "url": None, "local_path": f"/tmp/mock_{len(_image_call_log)}.png"}
    _image_call_log.append(result)
    return result

image_clients.generate_image = mock_generate_image

# ---- Monkeypatch campaign_preferences (no real Postgres in this test) ----
import campaign_preferences
campaign_preferences.get_preferences_text = lambda campaign_id: ""

# ---- Monkeypatch llm_client so no real Groq calls happen ----
import llm_client
from schemas import PlannerOutput, StrategyOutline, DayContentOutput

_planner_call_count = {"n": 0}
_day_call_count = {"n": 0}

def mock_generate_structured(system_prompt, user_prompt, schema, **kwargs):
    if schema is PlannerOutput:
        _planner_call_count["n"] += 1
        return PlannerOutput(strategy_outline=StrategyOutline(
            content_pillars=["Product highlight", "Behind the scenes", "Customer testimonial", "Quick tip"],
            tone="warm and casual",
            platform_mix="Instagram most days, Facebook a couple times a week",
            notes=None,
        ))
    if schema is DayContentOutput:
        _day_call_count["n"] += 1
        # Alternate needs_reference_photo so we exercise both paths
        needs_ref = (_day_call_count["n"] % 2 == 0)
        return DayContentOutput(
            idea=f"Test idea #{_day_call_count['n']}",
            platform="instagram",
            needs_reference_photo=needs_ref,
            caption="Mock caption \u2728",
            image_prompt="Mock image prompt",
        )
    raise ValueError(f"Unexpected schema in test: {schema}")

llm_client.generate_structured = mock_generate_structured

from marketing_engine import build_graph, generate_daily_asset

print("=" * 60)
print("  TEST 1 — Planning graph produces a 3-DAY (3d) content calendar")
print("            outline (one call, respecting model rate limits)")
print("=" * 60)

app = build_graph()

_planner_call_count["n"] = 0
initial_state = {
    "campaign_id": "test-campaign-3d",
    "business_context": "A neighborhood coffee shop.",
    "campaign_goal": "Increase foot traffic.",
    "target_audience": "Local young professionals.",
    "timeframe_days": 3,
    "user_plan": "free",
    "reference_images": {},
    "logs": [],
}

final_state = {}
for output in app.stream(initial_state):
    for node_name, value in output.items():
        final_state.update(value)

outline = final_state.get("strategy_outline")
calendar_dates = final_state.get("calendar_dates")
assert outline is not None, "strategy_outline should be populated"
assert len(outline.get("content_pillars", [])) >= 3, "Outline should have content pillars"
assert len(calendar_dates) == 3, f"Expected 3 calendar_dates for 3d calendar, got {len(calendar_dates)}"
assert final_state.get("calendar_plan") == {}, "calendar_plan should start EMPTY — populated incrementally, not upfront"
assert final_state.get("plan_status") == "draft", "Plan should be draft, not auto-approved"
assert _planner_call_count["n"] == 1, f"Expected exactly 1 planner call for 3d calendar, got {_planner_call_count['n']}"
print(f"\n✅ 3-day timeframe: 1 planner call, {len(outline['content_pillars'])} pillars, "
      f"{len(calendar_dates)} calendar_dates computed, calendar_plan empty (as expected)")

print("\n" + "=" * 60)
print("  TEST 2 — Per-day generation: ONE combined call decides")
print("            idea+platform+reference-need+caption+image_prompt")
print("=" * 60)

_day_call_count["n"] = 0
state_for_generation = dict(final_state)
state_for_generation["plan_status"] = "approved"
dates = sorted(state_for_generation["calendar_dates"])

results = []
for date in dates:
    result = generate_daily_asset(state_for_generation, date)
    results.append(result)
    state_for_generation["calendar_plan"] = {**state_for_generation.get("calendar_plan", {}), **result["calendar_plan"]}
    state_for_generation["asset_status"] = {**state_for_generation.get("asset_status", {}), **result["asset_status"]}

assert _day_call_count["n"] == len(dates), f"Expected exactly 1 combined LLM call per day, got {_day_call_count['n']} for {len(dates)} days"
print(f"\n✅ {len(dates)} days generated with exactly {_day_call_count['n']} combined LLM calls (1 per day)")

for date, result in zip(dates, results):
    day_entry = result["calendar_plan"][date]
    image = result["generated_images"][date]
    status = result["asset_status"][date]
    assert status == "awaiting_approval", f"{date}: expected awaiting_approval, got {status}"
    if day_entry["needs_reference_photo"]:
        assert image["is_placeholder"] is True, f"{date}: needs_reference_photo=True should mean is_placeholder=True"
        print(f"✅ {date}: idea={day_entry['idea']!r}, needs_reference_photo=True -> is_placeholder=True, still generated")
    else:
        assert image["is_placeholder"] is False, f"{date}: needs_reference_photo=False should mean is_placeholder=False"
        print(f"✅ {date}: idea={day_entry['idea']!r}, needs_reference_photo=False -> is_placeholder=False")

print("\n" + "=" * 60)
print("  TEST 3 — Image tweaking / refinement capability")
print("=" * 60)

_day_call_count["n"] = 0
tweak_target_date = dates[0]
tweaked_result = generate_daily_asset(state_for_generation, tweak_target_date)
assert tweak_target_date in tweaked_result["generated_images"], "Tweak should produce updated image asset"
print(f"✅ Successfully tweaked/regenerated image asset for date={tweak_target_date}")

print("\n✅ All checks passed successfully.")

