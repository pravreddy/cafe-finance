# reconciler — reads the WhatsApp Desktop database on the Mac

Why: the app is the front door; this is the audit. It proves everything posted
in the finance groups is in the ledger, and it backfills the months before the
app existed. See `docs/03-reconciler.md`.

## First run (one hour, proves the whole idea)

```bash
python3 reconciler/read_whatsapp_mac.py --probe
python3 reconciler/read_whatsapp_mac.py --dump "CG fin" -n 20
```

If `--probe` says "Permission denied", give Full Disk Access to Terminal (or
`python3`) in System Settings → Privacy & Security, then rerun.

If `--dump` shows `NOT DOWNLOADED` next to images: WhatsApp → Settings →
Storage → turn media auto-download on, and scroll the group once so old
media is fetched.

## Send to the server

```bash
mkdir -p ~/.cafe-finance
cat > ~/.cafe-finance/config.json <<'EOF'
{
  "server": "https://ledger.cafegopala.in",
  "token": "<API_TOKEN from server/.env>",
  "groups": ["CG fin", "Krishna Kitchen Finance"]
}
EOF
python3 reconciler/read_whatsapp_mac.py --push --backfill --dry-run   # look first
python3 reconciler/read_whatsapp_mac.py --push --backfill             # one-off history
python3 reconciler/read_whatsapp_mac.py --push                        # incremental
```

Then install the launchd job (every 5 minutes) — instructions inside the plist.
Add an Uptime Kuma push monitor and put its URL in `heartbeat_url` so a silent
break is noticed within a day (same dead-man's-switch pattern as the backups).

## Known limits

- Mac on, WhatsApp running, media downloaded.
- The schema is Meta's; `--probe` tells you the moment a column moves.
- Same message is never sent twice (cursor per group in `~/.cafe-finance/state.json`);
  the server also dedups by `(group_name, wa_message_pk)` and by file hash.
