#!/bin/bash
# Write secrets.toml from environment variables before starting Streamlit

mkdir -p /app/.streamlit

cat > /app/.streamlit/secrets.toml <<EOF
demo_mode = ${DEMO_MODE:-false}

[google_oauth]
client_id = "${GOOGLE_OAUTH_CLIENT_ID:-}"
client_secret = "${GOOGLE_OAUTH_CLIENT_SECRET:-}"
redirect_uri = "${GOOGLE_OAUTH_REDIRECT_URI:-http://localhost:8501}"

[gcp_credentials]
refresh_token = "${GCP_REFRESH_TOKEN:-}"
client_id = "${GCP_CLIENT_ID:-}"
client_secret = "${GCP_CLIENT_SECRET:-}"

[postgresql]
host = "${DB_HOST:-localhost}"
port = ${DB_PORT:-5432}
user = "${DB_USER:-loop}"
password = "${DB_PASSWORD:-password}"
database = "${DB_NAME:-loop_core}"
EOF

# Start nginx (routes :8080 → Streamlit :8501 and FastAPI :8001)
nginx

# Start FastAPI extension API
uvicorn extension_api:app --host 0.0.0.0 --port 8001 &

# Start Streamlit (main process)
exec streamlit run app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true
