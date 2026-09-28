# Go-live runbook — cafe-finance → ledger.cafegopala.in

Do these in order. Each step says where it runs (**Mac**, **server**, **phone**)
and how to know it worked. Budget: about an hour of hands-on time, plus the
Ollama model pull and the WhatsApp history backfill running unattended.

---

## 0. Before you start (Mac)

```bash
cd ~/Projects/github/cafe-finance
git status                      # clean
git log --oneline | head -3     # e3149e1 or later
cd server && USE_SQLITE=1 DOC_AI_PROVIDER=mock python3 manage.py test   # 27 OK (needs Django: pip install -r requirements.txt in a venv)
```

---

## 1. GitHub repo + secrets (Mac)

Standard steps for every app — see `avyangah-infra/docs/new-repo.md`.

```bash
cd ~/Projects/github/cafe-finance
gh repo create pravreddy/cafe-finance --private --source=. --remote=origin --push

cd ~/Projects/github/avyangah-infra
bash bin/repo-secrets.sh pravreddy/cafe-finance     # sets UK_SERVER_IP, SSH_USER, SSH_PRIVATE_KEY, GHCR_TOKEN
```

**Check:** Actions tab → `build-test-deploy`: `build` and `test` green (image on
ghcr, migrations from scratch, 27 tests). `deploy` fails the first time — no cert
yet — expected. Continue.

---

## 2. DNS (wherever cafegopala.in is managed — same place as pos.cafegopala.in)

Add: `A  ledger  →  88.99.167.49`  (TTL 300).

**Check (Mac):** `dig +short ledger.cafegopala.in` → `88.99.167.49`. Wait until it does.

---

## 3. TLS certificate (Mac → server, one-time) — the shared avyangah-infra script

```bash
cd ~/Projects/github/avyangah-infra
bash certbot/issue-cert.sh ledger.cafegopala.in     # asks for your sudo password on the box
```

Issues into `/etc/letsencrypt`, so the box's certbot.timer renews it with every
other domain (avyangah-infra/docs/cert-renewal.md). Nothing cert-related lives in
this repo.

**Check:** `ssh hetzner-prod 'ls -l /mnt/nvme_data/ssl/ | grep ledger'` shows fullchain + privkey.

---

## 4. First deploy (Mac → server)

Either re-run the failed GitHub Action (Actions → the run → *Re-run failed jobs*)
**or** from the laptop:

```bash
bash deploy/deploy.sh
```

What it does on the server: creates `~/careai/.env.cafe-finance` (SECRET_KEY,
ALLOWED_HOSTS, **API_TOKEN — copy it from the output, you need it in step 8**),
creates DB `cafe_finance` on careai-postgres, pulls `llama3.2-vision` into the
shared Ollama if missing (~8 GB, several minutes), starts `cafe-finance` (migrates +
seeds) and `cafe-finance-worker`, installs the nginx route, adds the 02:15 cron.

If it says `no container named 'ollama'`: `ssh hetzner-prod docker ps | grep -i ollama`
and put the real name in `deploy/remote.sh` (one line), commit, re-run.

**Check:**
```bash
curl -s https://ledger.cafegopala.in/ping        # ok
ssh hetzner-prod 'docker ps --format "{{.Names}} {{.Status}}" | grep cafe-finance'   # both Up
ssh hetzner-prod 'docker logs cafe-finance-worker --tail 5'                          # "re-queued 0" / idle
ssh hetzner-prod 'grep API_TOKEN ~/careai/.env.cafe-finance'                        # keep this
```

---

## 5. Your login, then everyone else from Settings

One command, once — your owner login (the only thing done on the server):

```bash
ssh hetzner-prod 'docker exec -it cafe-finance python manage.py createsuperuser --username praveen'
```

Everything else is on **https://ledger.cafegopala.in/settings/** (only owners see
the ⚙ Settings link). Nothing in this app is ever done in `/admin/`.

- **People** → Srinivas → username `srinivas`, a password (8+ characters, tell him
  in person), tick *Can confirm*; WhatsApp ID = his number with 91. Same for
  Priyanka (no *Can confirm*). Add staff with no login.
- **Cafes** → Cafe Gopala is trading with both finance groups; cafes 2 and 3 stay
  "not open" until they open — then rename, tick *Trading*, move their group.
- **Categories** → add / rename / hide as needed.

**Check:** sign out, sign in as srinivas → home loads, no ⚙ Settings link.

---

## 6. First real proof — the test that matters (Mac browser, then phone)

Browser first (fast, no phone needed):
1. Sign in as srinivas → **＋ Share** → upload a real PhonePe/GPay screenshot from the group → *Read it*.
2. Home shows "Being read". Within 1–2 minutes (Ollama on CPU) it becomes an entry.
   `ssh hetzner-prod 'docker logs cafe-finance-worker --tail 3'` shows `proof 1 [done] 45.2s → #CF-000001`.
3. Open it → check amount, payee, UTR, date are right. Fix anything, pick category, **Save**.
4. Sign in as praveen → **Queue** → **Confirm**. **Reports** → it appears.
5. Upload the **same** screenshot again → "Already recorded on … by Srinivas — added your share to the trail". Only one entry exists.

If step 2 lands in "Could not be read": `docker logs cafe-finance-worker --tail 30`.
Usually Ollama name/model. Fix, then the proof is retried automatically (3 attempts).

If the numbers are wrong often: note which fields, and we tune the prompt or move
that field to "must retype" — that is expected for the first week.

---

## 7. Phones (Android) — Srinivas, Priyanka, you

**PWA (no APK, do this first):** Chrome → https://ledger.cafegopala.in → sign in →
⋮ → *Add to Home screen* → *Install*. Then in PhonePe: open any receipt → Share →
**Cafe Finance** is in the sheet. Same from WhatsApp: long-press image → Share → Cafe Finance.

**APK (if Cafe Finance is missing from the share sheet on some phone):**
```bash
cd ~/Projects/github/cafe-finance/mobile
./setup.sh                                                   # needs Android Studio + ANDROID_HOME, like FastPOS
adb install -r android/app/build/outputs/apk/debug/app-debug.apk   # or send the APK on WhatsApp and open it
```
Open the app once, sign in, then share from PhonePe / WhatsApp.

**Real-world test on the phone:** pay something small (even ₹1 to yourself) →
Share → Cafe Finance → after a minute it's in the list → *Share to WhatsApp* on
the done page → pick the group → the caption with `#CF-0000xx` posts.

---

## 8. Reconciler on the Mac (reads the WhatsApp groups, backfills history)

Full Disk Access first: System Settings → Privacy & Security → Full Disk Access →
add **Terminal** (and later `/usr/bin/python3` for launchd).

```bash
cd ~/Projects/github/cafe-finance
python3 reconciler/read_whatsapp_mac.py --probe                 # tables + your groups listed?
python3 reconciler/read_whatsapp_mac.py --dump "CG fin" -n 20   # last 20 messages, media "on disk"?
```

If images say `NOT DOWNLOADED`: WhatsApp → Settings → Storage → auto-download on,
and scroll the group so old media fetches.

If `--probe` says a table/column is MISSING, stop and send me the output — the
schema differs from what the script expects and needs a one-line fix.

```bash
mkdir -p ~/.cafe-finance && cat > ~/.cafe-finance/config.json <<'EOF'
{ "server": "https://ledger.cafegopala.in",
  "token": "<API_TOKEN from step 4>",
  "groups": ["CG fin", "Krishna Kitchen Finance"] }
EOF
python3 reconciler/read_whatsapp_mac.py --push --backfill --dry-run   # what it WOULD send
python3 reconciler/read_whatsapp_mac.py --push --backfill             # history in; Ollama chews for hours
python3 reconciler/read_whatsapp_mac.py --push                        # incremental from then on
```

**Check:** https://ledger.cafegopala.in/queue/ fills with *Imported — needs review*
rows over the next hours; the *Seen in WhatsApp, not in ledger* section shows
images Ollama couldn't turn into a payment.

Then every 5 minutes automatically:
```bash
cp reconciler/com.guhya.cafe-finance.reconciler.plist ~/Library/LaunchAgents/
sed -i '' "s|__REPO__|$PWD|" ~/Library/LaunchAgents/com.guhya.cafe-finance.reconciler.plist
launchctl load ~/Library/LaunchAgents/com.guhya.cafe-finance.reconciler.plist
tail -f ~/.cafe-finance/reconciler.log
```
Optional: an Uptime Kuma push monitor → its URL as `heartbeat_url` in config.json.

---

## 9. Income from FastPOS (server, once)

Get an owner token in FastPOS:
```bash
ssh hetzner-prod 'docker exec guhya-pos-api-blue python manage.py drf_create_token <cafegopala owner username>'
```
Settings → Cafes → Cafe Gopala → *FastPOS token* = that token → Save. Then:
```bash
ssh hetzner-prod 'docker exec cafe-finance python manage.py pull_fastpos --days 30'
```
**Check:** Reports → *Income by source* shows `pos`. Cron repeats nightly.

---

## 10. Email invoices (optional, when a vendor sends PDFs)

Gmail: App Password + a label "Cafe invoices" with a filter. Then on the Mac or the box:
```bash
IMAP_USER=you@gmail.com IMAP_PASSWORD='app password' IMAP_FOLDER='Cafe invoices' \
CF_SERVER=https://ledger.cafegopala.in CF_TOKEN=<API_TOKEN> CF_CAFE=cafegopala \
python3 mail/imap_pull.py --all --dry-run          # then without --dry-run; cron every 15 min
```

---

## 11. Backups (server) — the proofs are the evidence

Add the `cafe_finance_media` volume and a `pg_dump cafe_finance` to the existing
nightly backup in avyangah-infra/backup (same job that backs up guhya_pos).
**Check:** the Storage Box shows `cafe_finance_*.sql.gz` and the media folder next morning.

---

## Week-one checklist (real-world test)

- [ ] 20+ real screenshots shared by Srinivas/Priyanka from their phones, from both PhonePe and WhatsApp
- [ ] At least one PDF invoice and one photo of a handwritten bill
- [ ] Every duplicate share came back "already recorded" — no double entries (Reports → Entries, look for repeated UTRs)
- [ ] Reconciler backfill finished; unmatched count in the queue reviewed
- [ ] Praveen confirmed a week's entries; Reports for the month make sense against the group
- [ ] One "Type it in" cash entry; one partner transfer; Balance page shows it
- [ ] Note every field Ollama got wrong → we tune the prompt or force retype for that field

## Rollback

`ssh hetzner-prod 'cd ~/careai && docker compose -f docker-compose.yml -f docker-compose.cafe-finance.yml stop cafe-finance cafe-finance-worker'`
— nothing else on the box is touched. Data stays in the `cafe_finance` DB and media volume.
