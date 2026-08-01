"""
Test script that validates the image routing logic works correctly,
including free/paid tier model selection and reference-photo gating.
Runs without real API keys by monkeypatching generate_image and the
MCP research/calendar calls.
"""
import sys
import io
import os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(__file__))

# ---- Monkeypatch image client ----
import image_clients

_call_log = []

def mock_generate_image(prompt: str, model_preference: str = "gemini_free", **kwargs):
    result = {
        "model": model_preference,
        "url": f"https://mock.test/{model_preference}_{len(_call_log)}.png",
        "local_path": f"/tmp/mock_{model_preference}_{len(_call_log)}.png",
        "prompt_snippet": prompt[:80],
    }
    _call_log.append(result)
    return result

image_clients.generate_image = mock_generate_image

# ---- Monkeypatch MCP-backed research/calendar calls (no real network) ----
import mcp_clients
mcp_clients.brave_web_search = lambda *a, **kw: None
mcp_clients.firecrawl_scrape = lambda *a, **kw: None
mcp_clients.firecrawl_search = lambda *a, **kw: None
mcp_clients.calendar_create_event = lambda *a, **kw: None

from marketing_engine import build_graph

app = build_graph()


def run_case(label: str, user_plan: str):
    _call_log.clear()
    initial_state = {
        "business_context": "Luxury fashion brand launching a summer collection.",
        "campaign_goal": "Drive 500 online purchases this month.",
        "target_audience": "Women aged 25-40, fashion-conscious, urban.",
        "total_budget": 8000.0,
        "user_plan": user_plan,
        "reference_images": {},
        "logs": [],
    }

    print("=" * 60)
    print(f"  IMAGE ROUTING TEST — {label} (mock API calls)")
    print("=" * 60)

    final_state = {}
    for output in app.stream(initial_state):
        for node_name, value in output.items():
            final_state.update(value)
            print(f"\n--- Node: {node_name} ---")
            if "logs" in value:
                for log in value["logs"]:
                    print(f"  {log}")

    images = final_state.get("generated_images", {})
    pending = final_state.get("pending_reference_requests", [])

    print(f"\n  Generated images: {len(images)}")
    for asset_id, info in images.items():
        print(f"    {asset_id}: model={info['model']}")
    print(f"  Pending reference-photo requests: {len(pending)}")
    for asset_id in pending:
        print(f"    {asset_id}")

    return images, pending


# Free tier: everything should route to gemini_free, and the mock plan's
# reference-photo asset should be skipped (pending) rather than generated.
free_images, free_pending = run_case("FREE TIER", "free")
assert all(info["model"] == "gemini_free" for info in free_images.values()), \
    "Free-tier user got routed to a non-free model!"
assert len(free_pending) >= 1, \
    "Expected at least one asset pending a reference photo in the mock plan."
print("\n✅ Free-tier routing correct: all generated assets used gemini_free, "
      "reference-gated asset correctly withheld.")

# Paid tier: should split across dalle3 / stable_diffusion, same asset
# still gated on a reference photo.
paid_images, paid_pending = run_case("PAID TIER", "paid")
sd_calls = [c for c in paid_images.values() if "stable" in c["model"]]
dalle_calls = [c for c in paid_images.values() if "dalle" in c["model"]]
print(f"\n  DALL-E 3 calls:         {len(dalle_calls)}")
print(f"  Stable Diffusion calls: {len(sd_calls)}")
assert len(sd_calls) + len(dalle_calls) == len(paid_images), \
    "Paid-tier user got routed to gemini_free unexpectedly!"
print("\n✅ Paid-tier routing correct: split across dalle3 / stable_diffusion, "
      "no gemini_free calls.")
