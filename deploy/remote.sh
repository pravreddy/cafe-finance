#!/usr/bin/env bash
# remote.sh — RUNS ON THE SERVER (shipped + invoked by deploy.sh / CI). Idempotent.
# Only ever adds cafe-finance's DB/containers/route; graceful nginx reload gated by nginx -t.
set -euo pipefail
CAREAI="$HOME/careai"
CERT="/mnt/nvme_data/ssl/ledger.guhya.co.in-fullchain.pem"
DC="docker compose -f docker-compose.yml -f docker-compose.cafe-finance.yml"
cd "$CAREAI"

echo "==> 1/6 .env.cafe-finance"
if [ ! -f .env.cafe-finance ]; then
  SK="$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')"
  TK="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf 'SECRET_KEY=%s\nALLOWED_HOSTS=ledger.guhya.co.in\nCSRF_TRUSTED_ORIGINS=https://ledger.guhya.co.in\nAPI_TOKEN=%s\n' "$SK" "$TK" > .env.cafe-finance
  echo "    created ~/careai/.env.cafe-finance  (API_TOKEN for the Mac reconciler + mail puller: $TK)"
fi

echo "==> 2/6 ensure cafe_finance database exists"
docker exec careai-postgres psql -U careai -tAc "SELECT 1 FROM pg_database WHERE datname='cafe_finance'" | grep -q 1 \
  || docker exec careai-postgres psql -U careai -c "CREATE DATABASE cafe_finance OWNER careai;"

echo "==> 3/6 vision model on the shared Ollama"
if docker ps --format '{{.Names}}' | grep -qx ollama; then
  if ! docker exec ollama ollama list 2>/dev/null | grep -q 'llama3.2-vision'; then
    echo "    pulling llama3.2-vision (one-off, ~8GB — takes a while)"
    docker exec ollama ollama pull llama3.2-vision
  fi
else
  echo "    !! no container named 'ollama' — the worker will fail every read until it exists"
fi

echo "==> 4/6 pull + start web (migrates on boot), then the worker"
$DC pull cafe-finance cafe-finance-worker
$DC up -d cafe-finance
ok=0
for i in $(seq 1 30); do
  if docker exec cafe-finance curl -sf -H 'Host: ledger.guhya.co.in' http://localhost:8000/ping >/dev/null 2>&1; then
    ok=1; echo "    healthy"; break
  fi
  sleep 2
done
if [ "$ok" != 1 ]; then echo "    health check FAILED:"; docker logs cafe-finance --tail 40; exit 1; fi
$DC up -d cafe-finance-worker

echo "==> 5/6 nginx route"
if [ ! -f "$CERT" ]; then
  echo "    cert not found — run deploy/cert.sh from your laptop, then re-run deploy.sh. (containers are up; route pending)"
  exit 0
fi
cp /tmp/ledger.guhya.co.in.conf "$CAREAI/site-confs/ledger.guhya.co.in.conf"
docker exec careai-nginx nginx -t
docker exec careai-nginx nginx -s reload

echo "==> 6/6 daily POS income pull (cron 02:15)"
( crontab -l 2>/dev/null | grep -v 'cafe-finance pull_fastpos' ; echo "15 2 * * * docker exec cafe-finance python manage.py pull_fastpos --days 3 >> /tmp/cafe-finance-pull.log 2>&1 # cafe-finance pull_fastpos" ) | crontab -
echo "==> ✓ cafe-finance live at https://ledger.guhya.co.in"
