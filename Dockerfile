# StudyForge — container image for Render (or any Docker host).
FROM python:3.12-slim

# System deps: none required beyond what the wheels provide; keep the image small.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first (layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application.
COPY . .

# Render mounts a persistent disk at /data (see render.yaml) and injects $PORT.
ENV STUDYFORGE_DATA_DIR=/data \
    STUDYFORGE_CONFIG_DIR=/data/config

EXPOSE 8000

# Bind to the port Render provides ($PORT), defaulting to 8000 for local `docker run`.
CMD ["sh", "-c", "uvicorn studyforge.web:app --host 0.0.0.0 --port ${PORT:-8000}"]
