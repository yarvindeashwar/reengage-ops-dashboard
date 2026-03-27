FROM python:3.10-slim

WORKDIR /app

# Install system deps for psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Port set by Cloud Run
ENV PORT=8080

EXPOSE ${PORT}

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
