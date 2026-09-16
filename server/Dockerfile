FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Static into STATIC_ROOT for WhiteNoise. No DB needed at build time.
RUN SECRET_KEY=build USE_SQLITE=1 python manage.py collectstatic --noinput

EXPOSE 8000

# Migrate on boot (idempotent), seed the reference rows (idempotent), serve.
# Proof uploads go to MEDIA_ROOT=/data/media — mount a volume there.
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_basic && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --timeout 180"]
