"""
Full integration test for both Image AND Video routing.
Uses monkeypatched mock API calls — no real keys needed.
"""
import sys
import io
import os

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(__file__))

# ---- Monkeypatch image client ----
import image_clients

_image_calls = []
def mock_generate_image(prompt, model_preference="dalle3", **kw):
    result = {
        "model": model_preference,
        "url": f"https://mock/{model_preference}_{len(_image_calls)}.png",
        "local_path": f"/tmp/{model_preference}_{len(_image_calls)}.png",
    }
    _image_calls.append(result)
    return result

image_clients.generate_image = mock_generate_image

# ---- Monkeypatch video client ----
import video_clients

_video_calls = []
def mock_generate_video(script_or_prompt, model_preference="runway", **kw):
    result = {
        "model": model_preference,
        "video_id": f"vid_{model_preference}_{len(_video_calls)}",
        "task_id": f"task_{model_preference}_{len(_video_calls)}",
        "generation_id": f"gen_{model_preference}_{len(_video_calls)}",
        "status": "processing",
        "video_url": f"https://mock/{model_preference}_{len(_video_calls)}.mp4",
    }
    _video_calls.append({**result, "input": script_or_prompt[:60]})
    return result

def mock_pick_video_model(desc):
    from video_clients import _AVATAR_KEYWORDS, _CINEMATIC_KEYWORDS
    lower = desc.lower()
    if any(kw in lower for kw in _AVATAR_KEYWORDS):
        return "heygen"
    if any(kw in lower for kw in _CINEMATIC_KEYWORDS):
        return "runway"
    return "runway"

video_clients.generate_video = mock_generate_video
# Keep the real pick_video_model since it doesn't call APIs

# ---- Run the engine ----
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
print("  FULL ROUTING TEST: Images + Videos (mock API calls)")
print("=" * 60)

for output in app.stream(initial_state):
    for node_name, value in output.items():
        print(f"\n--- Node: {node_name} ---")
        if "logs" in value:
            for log in value["logs"]:
                print(f"  {log}")
        if "generated_images" in value:
            print("\n  Generated Images:")
            for aid, info in value["generated_images"].items():
                print(f"    {aid}: model={info['model']}")
        if "generated_videos" in value:
            print("\n  Generated Videos:")
            for aid, info in value["generated_videos"].items():
                print(f"    {aid}: model={info['model']}, status={info.get('status')}")
        if "budget_allocations" in value:
            print("\n  Budget Allocations:")
            for platform, amount in value["budget_allocations"].items():
                print(f"    {platform}: ${amount:.2f}")
        if "publishing_status" in value:
            print("\n  Deployed Campaigns & Budgets:")
            for platform, info in value["publishing_status"].items():
                print(f"    [{platform.upper()}]: status={info.get('status')} | budget=${info.get('budget_allocated_usd', 0.0):.2f}")

print("\n" + "=" * 60)
print("  SUMMARY")
print("=" * 60)
print(f"  Image API calls:  {len(_image_calls)}")
for c in _image_calls:
    print(f"    [{c['model']:>20s}]")
print(f"  Video API calls:  {len(_video_calls)}")
for c in _video_calls:
    print(f"    [{c['model']:>12s}] input: {c['input']}...")
print("\nDone!")
