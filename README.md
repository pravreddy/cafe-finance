# cafe-finance

Income & expense ledger for three cafes, with **proof behind every rupee** — made *from* WhatsApp, because that is where the business actually runs.

Not part of FastPOS. FastPOS owns sales; this project owns expenses, invoices,
partner contributions and the evidence (payment screenshots, invoice PDFs) that
today lives only in WhatsApp groups ("CG fin", "Krishna Kitchen Finance") and email.

**Read [STORY.md](STORY.md) first** — the whole design in one file.

## Principle

> **We cannot make WhatsApp work as per our requirement. We can make our app
> work as per what we want.** WhatsApp stays exactly as it is; every rule,
> check and report lives on our side.

## The one-line idea

> Put our own app **in the share sheet**, next to WhatsApp, so every proof reaches
> us from two directions — *pay → share to Cafe Finance → app posts to the group*,
> and *already in WhatsApp → share to Cafe Finance* — plus a **reconciler** that
> reads the WhatsApp Desktop database on the Mac and flags anything that never
> reached the ledger. Belt and braces; nothing depends on one path working every day.

## Shared many times, counted once

- every share is a **ShareEvent** (who, from where, when, caption)
- all of them attach to **one** LedgerEntry, matched on payment ID (UTR, then app
  txn ID), then file hash, then `#CF` tag, then amount + payment date + comment
- a duplicate share is never an error — the person is told it was already recorded and by whom

## Payments vs invoices

A payment is money that **moved** (UPI screenshot). An invoice is money **owed**
(PDF from email, photo of a bill). Stored separately; one invoice is settled by
one or more payments (installments). Both arrive through the same door.

## Repo layout

| Path | What |
|---|---|
| `server/` | Django app + PWA. Ledger, document AI, dedup, review queue, API for the scripts. |
| `reconciler/` | Mac script that reads WhatsApp Desktop's `ChatStorage.sqlite`; launchd plist. |
| `mail/` | IMAP puller for invoice attachments (Gmail label). |
| `mobile/` | Android app (Capacitor shell + ShareIn plugin): "Cafe Finance" in the share sheet. |
| `deploy/` | Hetzner stack: compose overlay, nginx conf, deploy.sh / cert.sh (guhya-pos pattern). |
| `docs/` | Design notes 01–05. |
| `.github/workflows/` | Build → test on fresh Postgres → deploy (guhya-pos pattern). |

## Run it locally

```bash
cd server
cp .env.example .env               # Ollama on the box by default; DOC_AI_PROVIDER=mock to try without AI
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
USE_SQLITE=1 python manage.py migrate
python manage.py seed_basic --with-users     # logins: praveen / srinivas / priyanka, password change-me
USE_SQLITE=1 python manage.py runserver
# → http://127.0.0.1:8000/  sign in, "+ Add", upload a PhonePe screenshot
USE_SQLITE=1 DOC_AI_PROVIDER=mock python manage.py test
```

Or with Docker: `docker compose up --build` (Postgres + app on :8020).

The share-sheet part needs HTTPS and Android Chrome: deploy to
`ledger.guhya.co.in`, open it, "Add to Home screen", and **Cafe Finance**
appears in the share sheet.

## Docs

- [STORY.md](STORY.md) — everything, in order
- [docs/01-why-and-options.md](docs/01-why-and-options.md) — what we rejected and why
- [docs/02-capture-app.md](docs/02-capture-app.md) — the share-target app, flows, screens
- [docs/03-reconciler.md](docs/03-reconciler.md) — reading the WhatsApp Desktop DB safely
- [docs/04-data-model.md](docs/04-data-model.md) — ledger, proofs, invoices, dedup, partner balance
- [docs/05-plan.md](docs/05-plan.md) — build order and open questions

## Status

2026-09-28 — collect: PWA share target + Android app (`mobile/`); read: queue worker on
Ollama; analyse: /reports/ (KPIs, categories, daily spend, 6-month trend, who paid, payees,
income by source, entries, CSV / CSV+proofs export), /balance/, /pnl/, month close,
salaries; income: `pull_fastpos` cron. 27 tests green. **Not yet pushed to GitHub or
deployed** — see deploy/README.md; three steps: DNS, cert.sh, push to main.
