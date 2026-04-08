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

# Start FastAPI extension API in background
uvicorn extension_api:app --host 0.0.0.0 --port 8001 &

# Start Streamlit in background
streamlit run app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true &
STREAMLIT_PID=$!

# Wait for BOTH Streamlit and FastAPI to be ready before starting nginx
for i in $(seq 1 60); do
    STREAMLIT_OK=false
    FASTAPI_OK=false
    curl -sf http://localhost:8501/_stcore/health > /dev/null 2>&1 && STREAMLIT_OK=true
    curl -sf http://localhost:8001/api/extension/health > /dev/null 2>&1 && FASTAPI_OK=true
    if $STREAMLIT_OK && $FASTAPI_OK; then
        echo "All services ready"
        break
    fi
    sleep 1
done

# Start nginx in foreground (Cloud Run main process)
nginx -g 'daemon off;' &
NGINX_PID=$!

# If Streamlit dies, exit so Cloud Run restarts the container
wait $STREAMLIT_PID
kill $NGINX_PID 2>/dev/null
exit 1
