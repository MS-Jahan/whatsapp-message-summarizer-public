FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg nano micro ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY app ./app
COPY config/users.yaml.example /app/users.yaml.example

# Defaults; override in Coolify
ENV DB_PATH=/data/summarizer.db \
    USERS_FILE=/config/users.yaml \
    LOG_LEVEL=INFO

VOLUME ["/data", "/config"]

CMD ["python", "-m", "app.worker"]
