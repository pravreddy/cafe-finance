# cafe-finance — the whole story in one file

*Everything in `docs/` condensed into one read. If you only read one file, read this.*

---

## 1. Where we started

The cafes are paid for by whoever is standing there — Srinivas, Priyanka, Praveen —
by UPI. The proof is a PhonePe / GPay screenshot dropped into a WhatsApp group
("CG fin", "Krishna Kitchen Finance") with a one-line caption: *"10,000 interim
payment to painter"*. Invoices come by email as PDFs, or as a photo of a bill in
some other WhatsApp group. Nothing is totalled. Nothing says who paid. Praveen
puts his own money in to keep the cafes running and cannot show it with proof.

FastPOS already knows the income side. Nobody knows the expense side.

## 2. The principle

> **We cannot make WhatsApp work as per our requirement. We can make our app
> work as per what we want.**

WhatsApp is a bad ledger but a good inbox. People *will* post the screenshot the
second they pay — that habit is not going to change and we do not fight it. We
make our app the ledger and demote WhatsApp to a place proofs arrive.

## 3. Reading the WhatsApp group — what we looked at

| Way | Verdict |
|---|---|
| Our own **share-target app** | ✅ Primary. The phone's share sheet is the one place we *can* put ourselves next to WhatsApp. |
| **WhatsApp Desktop DB on the Mac** (`ChatStorage.sqlite`, unencrypted) | ✅ Audit + history backfill. Zero ban risk — it reads a file WhatsApp already synced. |
| Manual "Export chat" zip | ⚠️ Fallback only. |
| Dedicated Android + notification listener | ⚠️ Kept in pocket. |
| Official Groups API (Meta, 2026) | ⚠️ Business-created groups, ≤8 people, needs Official Business Account, cannot attach to existing groups. Apply later, not v1. |
| Cloud API 1:1 number | ❌ Never sees groups. |
| Unofficial linked-device libraries | ❌ Ban risk. Last resort with a burner. |

## 4. How it works

### Two doors in, one audit

**Door 1 — pay, then share (the normal case).**
Srinivas pays the painter in PhonePe → Share → picks **Cafe Finance** instead of
WhatsApp. The app opens with the screenshot loaded; document AI has already read
amount, payee, UTR, transaction id, date and the note. He taps a category chip,
confirms who paid, adds a comment, hits **Save**. The app then opens the
system share sheet with the same image and a caption —

    ₹10,000 · Painting / repairs · 2nd installment for painting · #CF-000123

— and he picks the WhatsApp group. Same taps as today, one extra screen, and the
group sees exactly what it sees now.

**Door 2 — already in WhatsApp.**
Anything already in a group (posted from WhatsApp Web, by someone without the
app, or simply forgotten): long-press → Share → **Cafe Finance**. Same review
screen. Also works for a photo of a bill, or a PDF someone dropped in a group.

**Email.** A Gmail label ("Cafe invoices") is polled over IMAP; every PDF / image
attachment goes through the same door.

**The audit.** A script on the Mac reads WhatsApp Desktop's database every few
minutes, sends every group message (with the image when it is on disk) to the
server, and the server answers one question: *is this in the ledger?* Anything
that isn't shows up as "seen in WhatsApp, not in ledger" for a human to look
at. The same script walks the full history once, so the old months get into
the books without anyone re-posting anything.

### Payments and invoices are different things

A **payment** is money that moved (a UPI screenshot). An **invoice** is money
that is owed (a PDF from email, a photo of a bill). They are stored separately
and linked: one invoice is settled by one or more payments — *"2nd installment
for painting"* is the normal case, not the edge case. When a payment to a
vendor is reviewed, the app suggests that vendor's open invoices; pick one and
the invoice's "owed" figure drops. Nothing is paid until the payment is confirmed.

### Shared many times, counted once

The same proof will be shared more than once: in both groups, forwarded from
WhatsApp Web, re-sent "just in case". That is expected and never an error.

- Every arrival is a **ShareEvent** — who shared, from which channel, when, with
  what caption. This is the "shared on this day by this person" trail.
- All of them hang off **one** ledger entry, matched in this order:
  1. **UTR** (bank-level id, unique across India — not a heuristic)
  2. app transaction id
  3. file hash (the same bytes again)
  4. our own `#CF-000123` tag in the caption
  5. amount + payment date + comment, *only* when the receipt carried no id
  6. weak (amount + date ± 1 day + payee) → shown to a human, never auto-merged
- A receipt that carries an id and matches nothing is a **different payment**,
  however alike the amount and note look. Two ₹10,000 painter installments with
  the same note a day apart is exactly the trap.
- The person sees: *"Already recorded on 14 Sep by Srinivas — added your share
  to the trail."* Nobody learns to hesitate before sharing.

### Nothing counts until a human says so

The document AI fills the fields; a person spends five seconds confirming. Only
`confirmed` rows reach the reports. Confirmed money is never edited — a
correction is a reversal row plus a new row (same rule as FastPOS bills).
Confirmers are a flag on the person (Praveen, Srinivas).

### What the ledger gives back

- Monthly P&L per cafe: income (FastPOS sales by day) − confirmed expenses by category.
- **Partner balance**: per cafe, per month, what each person paid — with proof
  behind every rupee. This is the report Praveen wanted from the start.
- Open invoices and what is still owed.
- "Unproven": entries with no proof, and group posts with no entry.

## 5. What is built (2026-09-16)

`server/` — Django, own compose stack, Postgres, WhiteNoise, gunicorn; deploys
with the same GitHub Actions shape as guhya-pos.

- Models: Cafe, Person, Category, Invoice, LedgerEntry, Proof, ShareEvent, GroupPost.
- `ledger/extraction/` — Gemini (default when a key is set), Ollama (opt-in), mock (tests).
  One prompt, one JSON schema, classifies payment / invoice / other.
- `ledger/dedup.py` — the match order above. `ledger/services.py::ingest()` — the one door.
- Screens: sign-in, home (my entries), **/share/** (PWA share target + upload form),
  **/review/** (pre-filled form, category chips, invoice suggestions, share trail),
  **/done/** (caption + "Share to WhatsApp" via `navigator.share` with the image),
  **/queue/** (confirmers: payments, invoices, open invoices, unmatched group posts),
  invoices list and detail, proof files behind login, Django admin for everything.
- PWA: `manifest.json` with `share_target`, minimal `sw.js`, icons.
- API for the scripts: `/api/upload/` (mail), `/api/reconcile/` (Mac), token in `X-Api-Token`.
- `seed_basic` command; 15 tests covering dedup, invoices, reversal, permissions, HTTP.

`reconciler/read_whatsapp_mac.py` — stdlib only; `--probe`, `--dump`, `--push [--backfill]`;
launchd plist; heartbeat URL for a dead-man's switch.

`mail/imap_pull.py` — stdlib only; Gmail label → attachments → `/api/upload/`.

## 6. What is not built yet

- Reports (P&L, partner balance) — models carry everything needed; no screens.
- FastPOS income pull.
- Real login for phones (username/password now; OTP or per-person link later).
- The server-side compose/nginx stack in `avyangah-infra` and the DNS for `ledger.guhya.co.in`.
- Background extraction (it runs inline in the request today; fine at cafe volume,
  move to a queue if Gemini gets slow).
- Reconciler schema verified against the installed WhatsApp version (`--probe` is the first step).
- Capacitor wrap / iOS share extension (only if an iPhone appears).

## 7. Build order from here

1. `--probe` and `--dump` on the Mac → confirm the reconciler reads the two groups.
2. Deploy server to the box, run `seed_basic --with-users`, set a real `API_TOKEN` and `GEMINI_API_KEY`.
3. Install the PWA on three phones; run one week alongside the groups.
4. `--push --backfill` once; then launchd every 5 minutes; Uptime Kuma heartbeat.
5. Gmail label + cron for `imap_pull.py`.
6. Reports.

## 8. Open questions

- "CG fin" and "Krishna Kitchen Finance": two cafes, or one cafe with two groups?
  (`seed_basic` assumes two; it is one line to change.)
- Gemini (fast, paise per document) or Ollama (free, slow) as the default reader?
- Which Mac runs the reconciler long term — laptop, or a spare Mac mini as an appliance?
- Can any partner confirm another's entry, or Praveen only?
