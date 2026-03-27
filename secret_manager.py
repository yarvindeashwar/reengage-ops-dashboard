"""
Fetch secrets from Google Cloud Secret Manager.
Falls back to Streamlit secrets for local dev.
"""

import streamlit as st


def get_openai_api_key() -> str:
    """Get OpenAI API key — from Streamlit secrets or GCP Secret Manager."""
    # First check Streamlit secrets (local dev / Streamlit Cloud)
    if "openai_api_key" in st.secrets:
        return st.secrets["openai_api_key"]

    # Fall back to GCP Secret Manager
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = "projects/arboreal-vision-339901/secrets/OPENAI_API_KEY/versions/latest"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        raise RuntimeError(f"Could not fetch OpenAI API key: {e}")
