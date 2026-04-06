FROM python:3.10-slim

WORKDIR /app

# Install system deps for psycopg2 and nginx
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc nginx && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# nginx config
COPY nginx.conf /etc/nginx/nginx.conf

# Port set by Cloud Run
ENV PORT=8080

EXPOSE ${PORT}

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
