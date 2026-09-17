# Marketing Agent backend — one image, used for all three services
# (web, celery worker, celery beat). The actual process is chosen by
# the start command at deploy time, not baked into the image — this
# matches the render.yaml setup where each service overrides CMD.

FROM python:3.11-slim

# Prevents Python from writing .pyc files and buffers stdout/stderr,
# so logs show up immediately in Render/Docker logs instead of being
# buffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps needed to build psycopg (binary wheels usually cover this,
# but build-essential + libpq-dev avoids surprises on slim images)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first, separately from the app code, so Docker
# caches this layer and skips reinstalling on every code change
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the actual application code
COPY . .

# Only meaningful for the web service — workers don't listen on a port,
# but declaring it here is harmless and documents intent
EXPOSE 8000

# Default command — this runs the FastAPI web service.
# Render's render.yaml overrides this per-service with its own
# startCommand for the celery worker and celery beat services, so this
# default only matters if you run the image directly with no override
# (e.g. local `docker run` or `docker-compose` without a command set).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
