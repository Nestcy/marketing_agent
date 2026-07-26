# ---------------------------------------------------------
# Configuration & API Key Management
# ---------------------------------------------------------
# Load API keys from environment variables. 
# Never hardcode keys in source files.
#
# Usage:
#   Set these before running:
#     export OPENAI_API_KEY="sk-..."
#     export STABILITY_API_KEY="sk-..."
#     export GROQ_API_KEY="gsk_..."
#     export HEYGEN_API_KEY="..."
#     export RUNWAY_API_KEY="..."
#     export LUMA_API_KEY="..."
#     export FACEBOOK_ACCESS_TOKEN="..."
#     export TIKTOK_ACCESS_TOKEN="..."
#     export GOOGLE_ADS_DEVELOPER_TOKEN="..."
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
def openai_api_key() -> str:
    return get_key("OPENAI_API_KEY")

def stability_api_key() -> str:
    return get_key("STABILITY_API_KEY")

def groq_api_key() -> str:
    return get_key("GROQ_API_KEY")

def heygen_api_key() -> str:
    return get_key("HEYGEN_API_KEY")

def runway_api_key() -> str:
    return get_key("RUNWAY_API_KEY")

def luma_api_key() -> str:
    return get_key("LUMA_API_KEY")

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

# Output directory for generated assets
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "generated_assets")
os.makedirs(ASSETS_DIR, exist_ok=True)
