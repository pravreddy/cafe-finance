# 05 — Build order and status

Each step is usable on its own. Stop anywhere and still have value.

| # | Step | Status (2026-09-16) |
|---|---|---|
| 1 | **Server core** — Django, own compose stack, Postgres, models from 04, admin, `ingest()`, document AI (Gemini / Ollama / mock), review queue | ✅ written, 15 tests green, not deployed |
| 2 | **PWA capture app** — `share_target`, review screen, save & forward to WhatsApp, my entries | ✅ written; needs HTTPS deploy to appear in the share sheet |
| 3 | **Reconciler on the Mac** — `--probe` first, then `--push --backfill`, then launchd + dead-man's switch | ✅ script written; schema NOT yet verified on the real DB — `--probe` is the next concrete step |
| 4 | **Email** — Gmail label → IMAP → attachments → same door | ✅ script written; needs an App Password and a label |
| 5 | **Reports** — monthly P&L (FastPOS income pull), partner balance, open invoices, unproven list | ⬜ not started |
| 6 | Server stack in `avyangah-infra/stacks/cafe-finance/`, nginx conf, DNS `ledger.guhya.co.in` | ⬜ not started |
| 7 | Later: Capacitor wrap if iPhone ever appears; OBA application for the official Groups API | ⬜ parked |

## Open questions

- Folder/domain name: `cafe-finance` / `ledger.guhya.co.in` — OK?
- Vision provider default: Gemini (fast, paise per document, key already in use for FastPOS)
  vs Ollama (free, slow)? Code defaults to Gemini when a key is set.
- Who confirms entries — Praveen only, or any partner can confirm another's? (`Person.can_confirm`)
- Do the two groups map to two cafes, or is "CG fin" the umbrella? (`seed_basic` assumes two)
- Which Mac runs the reconciler long term (laptop vs spare machine)?

## First concrete step

```bash
python3 reconciler/read_whatsapp_mac.py --probe
python3 reconciler/read_whatsapp_mac.py --dump "CG fin" -n 20
```

Confirms the tables, finds the two groups, prints the last 20 messages with
media paths. One hour, proves the reconciler before anything is deployed.
