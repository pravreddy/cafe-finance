#!/usr/bin/env bash
# cert.sh — RUN ON YOUR LAPTOP (one-time). Issues the TLS cert for ledger.guhya.co.in.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; SSH_HOST="${SSH_HOST:-hetzner-prod}"
scp "$HERE/remote-cert.sh" "$SSH_HOST:/tmp/cafe-finance-remote-cert.sh"
ssh -t "$SSH_HOST" "sudo bash /tmp/cafe-finance-remote-cert.sh"
echo "==> done. Now: bash deploy/deploy.sh"
