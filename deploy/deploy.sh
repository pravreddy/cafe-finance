#!/usr/bin/env bash
# deploy.sh — RUN ON YOUR LAPTOP. One command to deploy/update cafe-finance.
#   bash deploy/deploy.sh
# Prereqs (one-time): DNS A record ledger.guhya.co.in -> server IP, `bash deploy/cert.sh`,
# and the image on ghcr (pushed by this repo's CI on every main build).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SSH_HOST="${SSH_HOST:-hetzner-prod}"
echo "==> shipping stack to $SSH_HOST"
ssh "$SSH_HOST" 'mkdir -p ~/careai/site-confs'
scp "$HERE/docker-compose.cafe-finance.yml" "$SSH_HOST:~/careai/docker-compose.cafe-finance.yml"
scp "$HERE/ledger.guhya.co.in.conf"         "$SSH_HOST:/tmp/ledger.guhya.co.in.conf"
scp "$HERE/remote.sh"                       "$SSH_HOST:/tmp/cafe-finance-remote.sh"
echo "==> running remote deploy"
ssh "$SSH_HOST" "bash /tmp/cafe-finance-remote.sh"
echo
echo "==> test:  curl -s https://ledger.guhya.co.in/ping"
echo "==> first-time logins:  ssh $SSH_HOST 'docker exec -it cafe-finance python manage.py seed_basic --with-users'"
echo "==> then change the passwords in /admin/ (praveen / srinivas / priyanka start as change-me)"
