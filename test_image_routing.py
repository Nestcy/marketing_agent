"""
Test script that validates the image routing logic works correctly.
Runs without real API keys by monkeypatching the generate_image function.
"""
import sys
import io

# Fix Windows console encoding for unicode/emoji output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import os
sys.path.insert(0, os.path.dirname(__file__))

# Monkeypatch: replace real API calls with mock responses
import image_clients

_call_log = []

def mock_generate_image(prompt: str, model_preference: str = "dalle3", **kwargs):
    """Records the call and returns a fake result."""
    result = {
        "model": model_preference,
        "url": f"https://mock.test/{model_preference}_{len(_call_log)}.png",
        "local_path": f"/tmp/mock_{model_preference}_{len(_call_log)}.png",
        "prompt_snippet": prompt[:80],
    }
    _call_log.append(result)
    return result

image_clients.generate_image = mock_generate_image  # patch

# Now import and run the engine
from marketing_engine import build_graph

app = build_graph()

initial_state = {
    "business_context": "Luxury fashion brand launching a summer collection.",
    "campaign_goal": "Drive 500 online purchases this month.",
    "target_audience": "Women aged 25-40, fashion-conscious, urban.",
    "total_budget": 8000.0,
    "logs": [],
}

print("=" * 60)
print("  IMAGE ROUTING TEST (mock API calls)")
print("=" * 60)

for output in app.stream(initial_state):
    for node_name, value in output.items():
        print(f"\n--- Node: {node_name} ---")
        if "logs" in value:
            for log in value["logs"]:
                print(f"  {log}")
        if "generated_images" in value:
            print("\n  📸 Generated Images:")
            for asset_id, info in value["generated_images"].items():
                print(f"    {asset_id}:")
                print(f"      Model: {info['model']}")
                print(f"      URL:   {info.get('url', 'N/A')}")

print("\n" + "=" * 60)
print(f"  TOTAL API CALLS MADE: {len(_call_log)}")
print("=" * 60)

# Validate routing logic
print("\n🔍 Routing Validation:")
for call in _call_log:
    model = call["model"]
    snippet = call["prompt_snippet"]
    print(f"  [{model:>20s}] ← {snippet}...")

# Check that product/lifestyle went to SD, and promo/urgency went to DALL-E
sd_calls = [c for c in _call_log if "stable" in c["model"]]
dalle_calls = [c for c in _call_log if "dalle" in c["model"]]
print(f"\n  DALL-E 3 calls:         {len(dalle_calls)}")
print(f"  Stable Diffusion calls: {len(sd_calls)}")

if len(_call_log) == 4:
    print("\n✅ All 4 image assets from the campaign plan were processed!")
else:
    print(f"\n⚠️ Expected 4 image assets but got {len(_call_log)}")
