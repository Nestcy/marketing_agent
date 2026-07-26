# ---------------------------------------------------------
# Add this to config.py, alongside the other get_key()-based
# accessors (e.g. right after google_ads_customer_id()).
# ---------------------------------------------------------

def postgres_conn_string() -> str:
    """
    Connection string for the LangGraph Postgres checkpointer, used by
    campaign_manager.py to persist campaign state across restarts.

    Example:
        export POSTGRES_CONN_STRING="postgresql://user:password@host:5432/dbname?sslmode=require"
    """
    return get_key("POSTGRES_CONN_STRING")
