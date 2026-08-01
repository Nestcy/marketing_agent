# ---------------------------------------------------------
# Configuration & API Key Management
# ---------------------------------------------------------
# Load API keys from environment variables.
# Never hardcode keys in source files.
#
# Usage:
# Set these before running:
# export GEMINI_API_KEY="..."             # free-tier image gen (required)
# export OPENAI_API_KEY="sk-..."          # optional, paid-tier image gen
# export STABILITY_API_KEY="sk-..."       # optional, paid-tier image gen
# export GROQ_API_KEY="gsk_..."
# export GROQ_MODEL="...."
# export BRAVE_API_KEY="..."              # business research
# export FIRECRAWL_API_KEY="..."          # business research
# export BRAVE_SEARCH_MCP_URL="..."       # optional override, has a default
# export FIRECRAWL_MCP_URL="..."          # optional override, has a default
# export CALENDAR_MCP_URL="..."           # your chosen calendar MCP server
# export CALENDAR_MCP_API_KEY="..."
# export FACEBOOK_ACCESS_TOKEN="..."
# export TIKTOK_ACCESS_TOKEN="..."
# export GOOGLE_ADS_DEVELOPER_TOKEN="..."
# export POSTGRES_CONN_STRING="postgresql://user:password@host:5432/dbname?sslmode=require"
# export REDIS_URL="redis://..."          # Celery broker/backend, for daily publish cron
# ---------------------------------------------------------

import os


def get_key(name: str, required: bool = True) -> str:
    """Retrieve an API key from environment variables."""
    val = os.environ.get(name, "")
    if required and not val:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Set it with: export {name}=\"your-key-here\""
        )
    return val


# Lazy accessors — only fail when actually called

def google_api_key() -> str:
    return get_key("GOOGLE_API_KEY")


def genai_model() -> str:
    """
    Which Gemini image model to use. Defaults to the free-tier Flash
    image model if GENAI_MODEL isn't set — override in Railway's
    Variables tab to point at whatever model string is current.

    Example:
        export GENAI_MODEL="gemini-2.5-flash-image"
    """
    return os.environ.get("GENAI_MODEL", "gemini-2.5-flash-image")


def image_quality() -> str:
    """
    Passed through to Gemini's image generation config. Defaults to
    "standard" if IMAGE_QUALITY isn't set.

    Example:
        export IMAGE_QUALITY="high"
    """
    return os.environ.get("IMAGE_QUALITY", "standard")


def openai_api_key() -> str:
    return get_key("OPENAI_API_KEY")


def stability_api_key() -> str:
    return get_key("STABILITY_API_KEY")


def groq_api_key() -> str:
    return get_key("GROQ_API_KEY")


def groq_model() -> str:
    """
    Which Groq model to use for planning/ideation. Optional — falls back
    to a sensible default if GROQ_MODEL isn't set, so this never blocks
    startup the way a missing API key would.

    Example:
        export GROQ_MODEL="llama-3.3-70b-versatile"
    """
    return os.environ.get("GROQ_MODEL", "qwen/qwen3.6-27b")


def brave_api_key() -> str:
    return get_key("BRAVE_API_KEY")


def firecrawl_api_key() -> str:
    return get_key("FIRECRAWL_API_KEY")


def calendar_mcp_api_key() -> str:
    return get_key("CALENDAR_MCP_API_KEY")


def facebook_access_token() -> str:
    return get_key("FACEBOOK_ACCESS_TOKEN")


def facebook_ad_account_id() -> str:
    return get_key("FACEBOOK_AD_ACCOUNT_ID")


def tiktok_access_token() -> str:
    return get_key("TIKTOK_ACCESS_TOKEN")


def tiktok_advertiser_id() -> str:
    return get_key("TIKTOK_ADVERTISER_ID")


def google_ads_developer_token() -> str:
    return get_key("GOOGLE_ADS_DEVELOPER_TOKEN")


def google_ads_customer_id() -> str:
    return get_key("GOOGLE_ADS_CUSTOMER_ID")


def postgres_conn_string() -> str:
    """
    Connection string for the LangGraph Postgres checkpointer, used by
    campaign_manager.py to persist campaign state across restarts, and
    for the lightweight campaign_registry table used to list active
    campaigns for the daily Celery publish task.

    Example:
        export POSTGRES_CONN_STRING="postgresql://user:password@host:5432/dbname?sslmode=require"
    """
    return get_key("POSTGRES_CONN_STRING")


def redis_url() -> str:
    """
    Celery broker/result-backend URL. Railway's Redis plugin exposes
    this as REDIS_URL automatically when you attach a Redis instance
    to the service.

    Example:
        export REDIS_URL="redis://default:password@host:6379"
    """
    return get_key("REDIS_URL")


# Output directory for generated assets
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "generated_assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
