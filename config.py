# ---------------------------------------------------------
# Configuration & API Key Management
# ---------------------------------------------------------
# Load API keys from environment variables. Never hardcode keys.
#
# export GOOGLE_API_KEY="..."            # free-tier image gen (required)
# export GENAI_MODEL="..."               # optional, defaults below
# export IMAGE_QUALITY="high"            # optional
# export OPENAI_API_KEY="sk-..."         # optional, paid-tier image gen
# export STABILITY_API_KEY="sk-..."      # optional, paid-tier image gen
# export GROQ_API_KEY="gsk_..."
# export GROQ_MODEL="...."
# export TAVILY_API_KEY="tvly-..."       # business research (search)
# export FIRECRAWL_API_KEY="..."         # business research (scrape)
# export FACEBOOK_ACCESS_TOKEN="..."     # organic posting
# export TIKTOK_ACCESS_TOKEN="..."
# export INSTAGRAM_ACCESS_TOKEN="..."
# export POSTGRES_CONN_STRING="postgresql://user:password@host:5432/dbname?sslmode=require"
# export REDIS_URL="redis://..."         # Celery broker/backend, for daily cron
# ---------------------------------------------------------

import os


def get_key(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Set it with: export {name}=\"your-key-here\""
        )
    return val


def google_api_key() -> str:
    return get_key("GOOGLE_API_KEY")


def genai_model() -> str:
    return os.environ.get("GENAI_MODEL", "gemini-2.5-flash-image")


def image_quality() -> str:
    return os.environ.get("IMAGE_QUALITY", "standard")


def openai_api_key() -> str:
    return get_key("OPENAI_API_KEY")


def stability_api_key() -> str:
    return get_key("STABILITY_API_KEY")


def groq_api_key() -> str:
    return get_key("GROQ_API_KEY")


def groq_model() -> str:
    return os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")


def tavily_api_key() -> str:
    return get_key("TAVILY_API_KEY")


def firecrawl_api_key() -> str:
    return get_key("FIRECRAWL_API_KEY")


def postgres_conn_string() -> str:
    return get_key("POSTGRES_CONN_STRING")


def redis_url() -> str:
    return get_key("REDIS_URL")


ASSETS_DIR = os.path.join(os.path.dirname(__file__), "generated_assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
