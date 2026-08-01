# ---------------------------------------------------------
# Image Generation Clients
# Wraps DALL-E 3 (OpenAI), Stable Diffusion (Stability AI), and
# Gemini 2.5 Flash Image ("Nano Banana") — the free-tier default.
# ---------------------------------------------------------
import os
import base64
import time
import requests
from typing import Dict, Any, Optional

from config import openai_api_key, stability_api_key, google_api_key, genai_model, image_quality, ASSETS_DIR


# =========================================================
# Gemini 2.5 Flash Image ("Nano Banana") — FREE TIER
# =========================================================
class GeminiImageClient:
    """
    Free-tier image generation via a Gemini image-capable model.

    At time of writing, Gemini's Flash-tier image models offer a
    generous free quota via a Google AI Studio API key, no credit card
    required. Google can and does adjust quotas without notice — treat
    this as "generous for demo/low-volume use," not a guaranteed SLA.

    Model and quality are both configurable via env vars (set in
    Railway's Variables tab) rather than hardcoded, so you can point
    this at whatever model string is current without a code change:
        GOOGLE_API_KEY   — required
        GENAI_MODEL      — defaults to "gemini-2.5-flash-image"
        IMAGE_QUALITY    — defaults to "standard"

    Get a key: https://aistudio.google.com/apikey
    Docs: https://ai.google.dev/gemini-api/docs/image-generation
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self):
        self.api_key = google_api_key()
        self.model = genai_model()
        self.quality = image_quality()

    def generate(
        self,
        prompt: str,
        reference_image: Optional[bytes] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        """
        Args:
            prompt:            Text description of the image to generate.
            reference_image:   Optional raw image bytes for image-to-image
                                editing (e.g. a business-supplied product
                                photo the output should be based on).

        Returns:
            dict with keys: model, url (None — no hosted URL), local_path
        """
        url = f"{self.BASE_URL}/{self.model}:generateContent?key={self.api_key}"

        parts = [{"text": prompt}]
        if reference_image:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64encode(reference_image).decode("utf-8"),
                }
            })

        payload = {
            "contents": [{"parts": parts}],
            # NOTE: the exact field name/shape for quality control on
            # Gemini's image generation config isn't something I can
            # verify from memory — this is my best guess at the current
            # shape. If this 400s, check the current request schema at
            # https://ai.google.dev/gemini-api/docs/image-generation
            # and adjust generationConfig accordingly.
            "generationConfig": {"imageConfig": {"quality": self.quality}},
        }

        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code == 400:
            # Likely an unrecognized generationConfig field — retry as
            # plain text-to-image without the quality hint rather than
            # hard-failing the whole request over a speculative param.
            resp = requests.post(url, json={"contents": [{"parts": parts}]}, timeout=60)
        if resp.status_code == 429:
            raise RuntimeError(
                "Gemini free-tier rate limit hit (daily quota or RPM cap). "
                "Fall back to a paid model_preference or wait for reset."
            )
        resp.raise_for_status()
        data = resp.json()

        image_parts = [
            p for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p
        ]
        if not image_parts:
            raise RuntimeError(f"Gemini returned no image. Response: {data}")

        image_bytes = base64.b64decode(image_parts[0]["inlineData"]["data"])
        local_path = self._save(image_bytes)

        return {
            "model": self.model,
            "url": None,
            "local_path": local_path,
        }

    def _save(self, image_bytes: bytes) -> str:
        filename = f"gemini_{int(time.time())}.png"
        filepath = os.path.join(ASSETS_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        return filepath


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
        **_ignored,
    ) -> Dict[str, Any]:
        """
        Generate an image with DALL-E 3.

        Note: DALL-E 3 doesn't support image-to-image editing via this
        endpoint, so a `reference_image` passed through generate_image()
        is silently ignored here — use "gemini_free" or "stable_diffusion"
        for reference-image-based generation.

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
        local_path = self._download(image_url, "dalle3")

        return {
            "model": "dalle3",
            "url": image_url,
            "revised_prompt": revised_prompt,
            "local_path": local_path,
        }

    def _download(self, url: str, prefix: str) -> str:
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
        reference_image: Optional[bytes] = None,
        **_ignored,
    ) -> Dict[str, Any]:
        """
        Generate an image with Stable Diffusion 3.x via Stability AI.
        Pass `reference_image` bytes to use image-to-image mode instead
        of pure text-to-image.

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

        if reference_image:
            form_data["image"] = ("reference.png", reference_image, "image/png")
            form_data["mode"] = (None, "image-to-image")
            form_data["strength"] = (None, "0.65")

        resp = requests.post(self.API_URL, headers=headers, files=form_data, timeout=120)
        resp.raise_for_status()

        filename = f"sd3_{int(time.time())}.{output_format}"
        filepath = os.path.join(ASSETS_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(resp.content)

        result_seed = resp.headers.get("x-seed", seed)

        return {
            "model": f"stability_{model}",
            "local_path": filepath,
            "seed": result_seed,
            "url": None,
        }


# =========================================================
# Unified helper used by the LangGraph node
# =========================================================
def generate_image(
    prompt: str,
    model_preference: str = "gemini_free",
    reference_image: Optional[bytes] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Unified entry point for image generation.

    Args:
        prompt:            The image description / prompt.
        model_preference:  "gemini_free" (default, free tier) | "dalle3" |
                            "stable_diffusion"
        reference_image:   Optional raw image bytes to base generation on
                            (e.g. a business-supplied product photo).
                            Supported by "gemini_free" and
                            "stable_diffusion"; ignored by "dalle3".
        **kwargs:          Passed through to the underlying client.

    Returns:
        dict with generation results (model, url/local_path, etc.)
    """
    if model_preference == "gemini_free":
        client = GeminiImageClient()
        return client.generate(prompt, reference_image=reference_image, **kwargs)
    elif model_preference == "dalle3":
        client = DallE3Client()
        return client.generate(prompt, **kwargs)
    elif model_preference in ("stable_diffusion", "sd", "stability"):
        client = StabilityClient()
        return client.generate(prompt, reference_image=reference_image, **kwargs)
    else:
        raise ValueError(
            f"Unknown image model: {model_preference}. "
            f"Use 'gemini_free', 'dalle3', or 'stable_diffusion'."
        )
