"""
Smoke test for the v3 architecture: the planning graph (research ->
calendar draft) and the standalone daily asset generator. Runs without
real API keys by monkeypatching image_clients, research_clients, and
llm_client.
"""
import sys
import io
import os
import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
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

# ---- Monkeypatch llm_client so no real Groq calls happen ----
import llm_client
<<<<<<< HEAD
from schemas import PlannerOutput, CalendarPlan, DayPlanEntry, DayContentOutput
=======
from schemas import PlannerOutput, CalendarPlan, DayPlan, DayContentOutput
>>>>>>> 4b0816895dfa61dc15c9d672ff553381b4526119

def mock_generate_structured(system_prompt, user_prompt, schema, **kwargs):
    if schema is PlannerOutput:
        today = datetime.date.today()
<<<<<<< HEAD
        entries = []
        for i in range(3):  # small calendar for the test
            date = (today + datetime.timedelta(days=i)).isoformat()
            entries.append(DayPlanEntry(
                date=date,
                idea=f"Test idea {i}",
                platform="instagram",
                needs_reference_photo=(i == 1),  # middle day needs a reference photo
            ))
        return PlannerOutput(calendar_plan=CalendarPlan(days=entries))
=======
        days = {}
        for i in range(3):  # small calendar for the test
            date = (today + datetime.timedelta(days=i)).isoformat()
            days[date] = DayPlan(
                idea=f"Test idea {i}",
                platform="instagram",
                needs_reference_photo=(i == 1),  # middle day needs a reference photo
            )
        return PlannerOutput(calendar_plan=CalendarPlan(days=days))
>>>>>>> 4b0816895dfa61dc15c9d672ff553381b4526119
    if schema is DayContentOutput:
        return DayContentOutput(caption="Mock caption \u2728", image_prompt="Mock image prompt")
    raise ValueError(f"Unexpected schema in test: {schema}")

llm_client.generate_structured = mock_generate_structured

# ---- Monkeypatch campaign_preferences (no real Postgres in this test) ----
import campaign_preferences
campaign_preferences.get_preferences_text = lambda campaign_id: ""

from marketing_engine import build_graph, generate_daily_asset

print("=" * 60)
print("  TEST 1 — Planning graph produces a day-keyed calendar")
print("=" * 60)

app = build_graph()
initial_state = {
    "campaign_id": "test-campaign",
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
        print(f"\n--- Node: {node_name} ---")
        for log in value.get("logs", []):
            print(f"  {log}")

calendar = final_state.get("calendar_plan", {})
assert len(calendar) == 3, f"Expected 3 days in calendar, got {len(calendar)}"
assert final_state.get("plan_status") == "draft", "Plan should be in draft status, not auto-approved"
print(f"\n✅ Calendar has {len(calendar)} days, plan_status='draft' (awaiting approval, as expected)")

print("\n" + "=" * 60)
print("  TEST 2 — Daily asset generation (per-day, standalone)")
print("=" * 60)

dates = sorted(calendar.keys())
normal_day, reference_day = dates[0], dates[1]

state_for_generation = dict(final_state)
state_for_generation["plan_status"] = "approved"  # simulate approval

# Day without a reference-photo requirement — should generate fully
result_normal = generate_daily_asset(state_for_generation, normal_day)
assert result_normal["asset_status"][normal_day] == "awaiting_approval", \
    f"Expected awaiting_approval, got {result_normal['asset_status']}"
print(f"\n✅ {normal_day}: generated normally -> awaiting_approval")

# Day WITH a reference-photo requirement, none supplied yet — should be withheld
result_pending = generate_daily_asset(state_for_generation, reference_day)
assert result_pending["asset_status"][reference_day] == "pending_generation", \
    f"Expected pending_generation, got {result_pending['asset_status']}"
assert "generated_images" not in result_pending, "Should not have generated an image without the reference photo"
print(f"✅ {reference_day}: correctly withheld pending a reference photo (caption still drafted)")

print("\n✅ All checks passed.")
<<<<<<< HEAD

print("\n" + "=" * 60)
print("  TEST 3 — Multi-chunk planning (90 days, one chunk fails)")
print("=" * 60)

from marketing_engine import _CALENDAR_CHUNK_SIZE

call_count = {"n": 0}

def mock_generate_structured_chunked(system_prompt, user_prompt, schema, **kwargs):
    if schema is not PlannerOutput:
        return mock_generate_structured(system_prompt, user_prompt, schema, **kwargs)

    call_count["n"] += 1
    # Simulate the 2nd chunk failing outright, like a real tool_use_failed error
    if call_count["n"] == 2:
        raise RuntimeError("simulated tool_use_failed")

    # Pull the chunk's dates back out of the prompt (crude but fine for a test)
    import re
    dates_in_prompt = re.findall(r"\d{4}-\d{2}-\d{2}", user_prompt)
    entries = [
        DayPlanEntry(date=d, idea=f"Idea for {d}", platform="instagram", needs_reference_photo=False)
        for d in dates_in_prompt
    ]
    return PlannerOutput(calendar_plan=CalendarPlan(days=entries))

llm_client.generate_structured = mock_generate_structured_chunked

state_90 = dict(initial_state)
state_90["timeframe_days"] = 90
final_state_90 = {}
for output in app.stream(state_90):
    for node_name, value in output.items():
        final_state_90.update(value)
        if node_name == "master_planner_node":
            for log in value.get("logs", []):
                print(f"  {log}")

calendar_90 = final_state_90.get("calendar_plan", {})
expected_chunks = -(-90 // _CALENDAR_CHUNK_SIZE)  # ceil division
assert len(calendar_90) == 90, f"Expected 90 days total (chunk failure should still fall back, not drop days), got {len(calendar_90)}"
assert call_count["n"] == expected_chunks, f"Expected {expected_chunks} chunk calls, got {call_count['n']}"
print(f"\n✅ 90-day calendar has all 90 days despite chunk 2 failing outright ({expected_chunks} chunk calls made, chunk 2 fell back correctly)")

llm_client.generate_structured = mock_generate_structured  # restore for any later use

print("\n✅ All checks passed.")
=======
>>>>>>> 4b0816895dfa61dc15c9d672ff553381b4526119
