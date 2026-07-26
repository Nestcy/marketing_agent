# ---------------------------------------------------------
# Image Generation Clients
# Wraps DALL-E 3 (OpenAI) and Stable Diffusion (Stability AI)
# ---------------------------------------------------------
import os
import base64
import time
import requests
from typing import Dict, Any, Optional

from config import openai_api_key, stability_api_key, ASSETS_DIR


# =========================================================
# DALL-E 3  (via OpenAI API)
# =========================================================
class DallE3Client:
    """
    Generates images using OpenAI's DALL-E 3 model.
    
    Best for:
      - Creative / artistic brand imagery
      - Abstract concepts and illustrations
      - Social media hero graphics
      - Ad banners with specific compositions
    """

    API_URL = "https://api.openai.com/v1/images/generations"

    def __init__(self):
        self.api_key = openai_api_key()

    def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "hd",
        style: str = "vivid",
        n: int = 1,
    ) -> Dict[str, Any]:
        """
        Generate an image with DALL-E 3.

        Args:
            prompt:  Detailed description of the image to generate.
            size:    "1024x1024" | "1024x1792" | "1792x1024"
            quality: "standard" | "hd"
            style:   "vivid" | "natural"
            n:       Number of images (DALL-E 3 only supports n=1).

        Returns:
            dict with keys: model, url, revised_prompt, local_path
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "dall-e-3",
            "prompt": prompt,
            "size": size,
            "quality": quality,
            "style": style,
            "n": n,
        }

        resp = requests.post(self.API_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        image_url = data["data"][0]["url"]
        revised_prompt = data["data"][0].get("revised_prompt", prompt)

        # Download and save locally
        local_path = self._download(image_url, "dalle3")

        return {
            "model": "dalle3",
            "url": image_url,
            "revised_prompt": revised_prompt,
            "local_path": local_path,
        }

    def _download(self, url: str, prefix: str) -> str:
        """Download image to local assets dir."""
        filename = f"{prefix}_{int(time.time())}.png"
        filepath = os.path.join(ASSETS_DIR, filename)
        img_resp = requests.get(url, timeout=60)
        img_resp.raise_for_status()
        with open(filepath, "wb") as f:
            f.write(img_resp.content)
        return filepath


# =========================================================
# Stable Diffusion  (via Stability AI REST API)
# =========================================================
class StabilityClient:
    """
    Generates images using Stability AI's Stable Diffusion models.
    
    Best for:
      - Photorealistic product shots
      - Lifestyle and fashion imagery
      - Fine-grained control (cfg_scale, steps, sampler)
      - Image-to-image variations
    """

    API_URL = "https://api.stability.ai/v2beta/stable-image/generate/sd3"

    def __init__(self):
        self.api_key = stability_api_key()

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        aspect_ratio: str = "1:1",
        output_format: str = "png",
        model: str = "sd3.5-large",
        seed: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Generate an image with Stable Diffusion 3.x via Stability AI.

        Args:
            prompt:           Text description of image to generate.
            negative_prompt:  Things to exclude from the image.
            aspect_ratio:     "1:1" | "16:9" | "9:16" | "4:3" | "3:4" etc.
            output_format:    "png" | "jpeg" | "webp"
            model:            "sd3.5-large" | "sd3.5-medium" | "sd3.5-large-turbo"
            seed:             Optional seed for reproducibility.

        Returns:
            dict with keys: model, local_path, seed
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "image/*",
        }

        form_data = {
            "prompt": (None, prompt),
            "negative_prompt": (None, negative_prompt),
            "aspect_ratio": (None, aspect_ratio),
            "output_format": (None, output_format),
            "model": (None, model),
        }
        if seed is not None:
            form_data["seed"] = (None, str(seed))

        resp = requests.post(
            self.API_URL, headers=headers, files=form_data, timeout=120
        )
        resp.raise_for_status()

        # Save the raw image bytes
        filename = f"sd3_{int(time.time())}.{output_format}"
        filepath = os.path.join(ASSETS_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(resp.content)

        result_seed = resp.headers.get("x-seed", seed)

        return {
            "model": f"stability_{model}",
            "local_path": filepath,
            "seed": result_seed,
            "url": None,  # Stability returns bytes directly, no hosted URL
        }


# =========================================================
# Unified helper used by the LangGraph node
# =========================================================
def generate_image(
    prompt: str,
    model_preference: str = "dalle3",
    **kwargs,
) -> Dict[str, Any]:
    """
    Unified entry point for image generation.
    
    Args:
        prompt:            The image description / prompt.
        model_preference:  "dalle3" or "stable_diffusion"
        **kwargs:          Passed through to the underlying client.

    Returns:
        dict with generation results (model, url/local_path, etc.)
    """
    if model_preference == "dalle3":
        client = DallE3Client()
        return client.generate(prompt, **kwargs)
    elif model_preference in ("stable_diffusion", "sd", "stability"):
        client = StabilityClient()
        return client.generate(prompt, **kwargs)
    else:
        raise ValueError(f"Unknown image model: {model_preference}. Use 'dalle3' or 'stable_diffusion'.")
