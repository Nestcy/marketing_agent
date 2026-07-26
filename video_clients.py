# ---------------------------------------------------------
# Video & Avatar Generation Clients
# Wraps HeyGen, Synthesia (AI Avatars) and Runway, Luma (Text-to-Video)
# ---------------------------------------------------------
import os
import time
import requests
from typing import Dict, Any, Optional

from config import heygen_api_key, runway_api_key, luma_api_key, ASSETS_DIR


# =========================================================
# HeyGen  (AI Avatar Videos)
# =========================================================
class HeyGenClient:
    """
    Generates AI avatar talking-head videos via the HeyGen API.
    
    Best for:
      - Customer testimonial-style UGC
      - Product explainers with a human presenter
      - Personalized outreach videos
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(self):
        self.api_key = heygen_api_key()
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def list_avatars(self) -> list:
        """List available avatar characters."""
        resp = requests.get(
            f"{self.BASE_URL}/v2/avatars", headers=self.headers, timeout=30
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("avatars", [])

    def generate(
        self,
        script: str,
        avatar_id: str = "default",
        voice_id: str = "default",
        video_width: int = 1080,
        video_height: int = 1920,
    ) -> Dict[str, Any]:
        """
        Create a talking-head avatar video.

        Args:
            script:       The text the avatar will speak.
            avatar_id:    HeyGen avatar ID (use list_avatars to find them).
            voice_id:     HeyGen voice ID.
            video_width:  Output width in pixels.
            video_height: Output height in pixels.

        Returns:
            dict with keys: model, video_id, status, video_url
        """
        payload = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": avatar_id,
                        "avatar_style": "normal",
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script,
                        "voice_id": voice_id,
                    },
                }
            ],
            "dimension": {"width": video_width, "height": video_height},
        }

        resp = requests.post(
            f"{self.BASE_URL}/v2/video/generate",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        video_id = data.get("video_id", "unknown")

        return {
            "model": "heygen",
            "video_id": video_id,
            "status": "processing",
            "video_url": None,  # Must poll for completion
        }

    def check_status(self, video_id: str) -> Dict[str, Any]:
        """Poll for video generation status."""
        resp = requests.get(
            f"{self.BASE_URL}/v1/video_status.get",
            headers=self.headers,
            params={"video_id": video_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return {
            "status": data.get("status"),
            "video_url": data.get("video_url"),
        }


# =========================================================
# Synthesia  (AI Avatar Videos)
# =========================================================
class SynthesiaClient:
    """
    Generates AI avatar videos via the Synthesia API.
    
    Best for:
      - Professional / corporate style presentations
      - Multi-language avatar videos
      - Branded spokesperson videos
    """

    BASE_URL = "https://api.synthesia.io/v2"

    def __init__(self):
        # Synthesia uses the same env var pattern
        self.api_key = os.environ.get("SYNTHESIA_API_KEY", "")
        if not self.api_key:
            raise EnvironmentError("Missing SYNTHESIA_API_KEY")
        self.headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
        }

    def generate(
        self,
        script: str,
        avatar_id: str = "anna_costume1_cameraA",
        language: str = "en-US",
        title: str = "Marketing Campaign Video",
    ) -> Dict[str, Any]:
        """
        Create a Synthesia avatar video.

        Args:
            script:     The text the avatar will speak.
            avatar_id:  Synthesia avatar ID.
            language:   Language code.
            title:      Video title for the dashboard.

        Returns:
            dict with keys: model, video_id, status
        """
        payload = {
            "test": True,  # Set to False for production
            "title": title,
            "input": [
                {
                    "scriptText": script,
                    "avatar": avatar_id,
                    "language": language,
                }
            ],
        }

        resp = requests.post(
            f"{self.BASE_URL}/videos",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "model": "synthesia",
            "video_id": data.get("id", "unknown"),
            "status": data.get("status", "processing"),
            "video_url": None,
        }


# =========================================================
# Runway  (Text / Image to Video — cinematic)
# =========================================================
class RunwayClient:
    """
    Generates cinematic video clips via Runway's Gen API.
    
    Best for:
      - Cinematic B-roll footage
      - Product reveal animations
      - Abstract / mood-setting video clips
    """

    BASE_URL = "https://api.dev.runwayml.com/v1"

    def __init__(self):
        self.api_key = runway_api_key()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Runway-Version": "2024-11-06",
        }

    def generate(
        self,
        prompt: str,
        model: str = "gen4_turbo",
        duration: int = 5,
        ratio: str = "16:9",
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate a video from a text prompt.

        Args:
            prompt:   Detailed description of the video to generate.
            model:    "gen4_turbo" | "gen4"
            duration: Duration in seconds (5 or 10).
            ratio:    "16:9" | "9:16" | "1:1"
            seed:     Optional seed for reproducibility.

        Returns:
            dict with keys: model, task_id, status
        """
        payload = {
            "model": model,
            "promptText": prompt,
            "duration": duration,
            "ratio": ratio,
        }
        if seed is not None:
            payload["seed"] = seed

        resp = requests.post(
            f"{self.BASE_URL}/text_to_video",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "model": f"runway_{model}",
            "task_id": data.get("id", "unknown"),
            "status": "RUNNING",
            "video_url": None,
        }

    def check_status(self, task_id: str) -> Dict[str, Any]:
        """Poll for video generation status."""
        resp = requests.get(
            f"{self.BASE_URL}/tasks/{task_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        output_urls = data.get("output", [])
        return {
            "status": data.get("status"),
            "video_url": output_urls[0] if output_urls else None,
        }


# =========================================================
# Luma  (Dream Machine — Text to Video)
# =========================================================
class LumaClient:
    """
    Generates video clips via Luma AI's Dream Machine API.
    
    Best for:
      - Stylized / dreamlike video content
      - Creative social media shorts
      - Experimental marketing visuals
    """

    BASE_URL = "https://api.lumalabs.ai/dream-machine/v1"

    def __init__(self):
        self.api_key = luma_api_key()
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate(
        self,
        prompt: str,
        model: str = "ray-2",
        aspect_ratio: str = "16:9",
        loop: bool = False,
    ) -> Dict[str, Any]:
        """
        Generate a video from a text prompt.

        Args:
            prompt:       Detailed description of the video.
            model:        "ray-2" | "ray-flash-2"
            aspect_ratio: "16:9" | "9:16" | "1:1" | "4:3" | "3:4"
            loop:         Whether the video should loop seamlessly.

        Returns:
            dict with keys: model, generation_id, status
        """
        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "loop": loop,
        }

        resp = requests.post(
            f"{self.BASE_URL}/generations",
            headers=self.headers,
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            "model": f"luma_{model}",
            "generation_id": data.get("id", "unknown"),
            "status": data.get("state", "queued"),
            "video_url": None,
        }

    def check_status(self, generation_id: str) -> Dict[str, Any]:
        """Poll for video generation status."""
        resp = requests.get(
            f"{self.BASE_URL}/generations/{generation_id}",
            headers=self.headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        video_url = None
        assets = data.get("assets", {})
        if assets:
            video_url = assets.get("video")
        return {
            "status": data.get("state"),
            "video_url": video_url,
        }


# =========================================================
# Unified helper used by the LangGraph node
# =========================================================

# Keywords for routing
_AVATAR_KEYWORDS = {"testimonial", "ugc", "avatar", "talking", "spokesperson", "presenter", "explainer"}
_CINEMATIC_KEYWORDS = {"cinematic", "b-roll", "reveal", "mood", "abstract", "retargeting", "brand awareness"}

def pick_video_model(asset_description: str) -> str:
    """
    Heuristic: choose the best video model based on the asset description.
    
    Avatar content → HeyGen (default) or Synthesia
    Cinematic/B-roll → Runway (default) or Luma
    """
    lower = asset_description.lower()
    if any(kw in lower for kw in _AVATAR_KEYWORDS):
        return "heygen"
    if any(kw in lower for kw in _CINEMATIC_KEYWORDS):
        return "runway"
    # Default: use runway for general video needs
    return "runway"

def generate_video(
    script_or_prompt: str,
    model_preference: str = "runway",
    **kwargs,
) -> Dict[str, Any]:
    """
    Unified entry point for video generation.
    
    Args:
        script_or_prompt:  The script (for avatars) or text prompt (for text-to-video).
        model_preference:  "heygen" | "synthesia" | "runway" | "luma"
        **kwargs:          Passed through to the underlying client.

    Returns:
        dict with generation results (model, video_id/task_id, status, etc.)
    """
    if model_preference == "heygen":
        client = HeyGenClient()
        return client.generate(script=script_or_prompt, **kwargs)
    elif model_preference == "synthesia":
        client = SynthesiaClient()
        return client.generate(script=script_or_prompt, **kwargs)
    elif model_preference == "runway":
        client = RunwayClient()
        return client.generate(prompt=script_or_prompt, **kwargs)
    elif model_preference == "luma":
        client = LumaClient()
        return client.generate(prompt=script_or_prompt, **kwargs)
    else:
        raise ValueError(
            f"Unknown video model: {model_preference}. "
            f"Use 'heygen', 'synthesia', 'runway', or 'luma'."
        )
