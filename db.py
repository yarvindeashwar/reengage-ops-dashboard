"""
BigQuery client and query helpers.
"""

import pandas as pd
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.credentials import Credentials as OAuthCredentials

from config import PROJECT


@st.cache_resource
def bq_client():
    """Return a cached BigQuery client, supporting multiple auth modes."""
    # Streamlit Cloud: service account key
    if "gcp_service_account" in st.secrets:
        creds = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=["https://www.googleapis.com/auth/bigquery"],
        )
        return bigquery.Client(project=PROJECT, credentials=creds)
    # Streamlit Cloud: authorized_user OAuth
    if "gcp_credentials" in st.secrets:
        info = dict(st.secrets["gcp_credentials"])
        creds = OAuthCredentials(
            token=None,
            refresh_token=info["refresh_token"],
            token_uri="https://oauth2.googleapis.com/token",
            client_id=info["client_id"],
            client_secret=info["client_secret"],
        )
        creds.refresh(AuthRequest())
        return bigquery.Client(project=PROJECT, credentials=creds)
    # Local: use ADC
    return bigquery.Client(project=PROJECT)


def bq_read(sql: str) -> pd.DataFrame:
    return bq_client().query(sql).to_dataframe()


def bq_exec(sql: str):
    bq_client().query(sql).result()
