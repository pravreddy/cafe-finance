#!/usr/bin/env bash
# remote-cert.sh — RUNS ON THE SERVER with sudo. Same recipe as guhya-pos.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "must run as root"; exit 1; }
EMAIL="praveen.moj@gmail.com"; DOMAIN="ledger.cafegopala.in"
SSL_DIR="/mnt/nvme_data/ssl"; WEBROOT="/var/www/certbot"; SITES="/home/deploy/careai/site-confs"
if [ ! -f "${SSL_DIR}/${DOMAIN}-fullchain.pem" ]; then
  printf 'server {\n    listen 80;\n    server_name %s;\n    location /.well-known/acme-challenge/ { root %s; allow all; default_type "text/plain"; }\n    location / { return 301 https://$host$request_uri; }\n}\n' "$DOMAIN" "$WEBROOT" > "${SITES}/${DOMAIN}.conf"
  chown deploy:deploy "${SITES}/${DOMAIN}.conf"
  sudo -u deploy docker exec careai-nginx nginx -t && sudo -u deploy docker exec careai-nginx nginx -s reload
fi
certbot certonly --webroot --webroot-path "${WEBROOT}" --email "${EMAIL}" --agree-tos --no-eff-email \
  --keep-until-expiring --non-interactive -d "${DOMAIN}"
cp -L "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" "${SSL_DIR}/${DOMAIN}-fullchain.pem"
cp -L "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"   "${SSL_DIR}/${DOMAIN}-privkey.pem"
chown deploy:deploy "${SSL_DIR}/${DOMAIN}-"*.pem; chmod 644 "${SSL_DIR}/${DOMAIN}-fullchain.pem"; chmod 600 "${SSL_DIR}/${DOMAIN}-privkey.pem"
echo "cert installed for ${DOMAIN}"
