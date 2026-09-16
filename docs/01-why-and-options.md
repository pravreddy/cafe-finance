# 01 — Why, and the options we looked at

## The problem

Cafe expenses are paid by whoever is there (Srinivas, Priyanka, Praveen) by UPI,
and the proof is a PhonePe / GPay screenshot posted in a WhatsApp group with a
one-line caption ("10,000 interim payment to painter"). Invoices arrive by email.
Nothing is totalled, categorised, or attributed to who paid. Praveen also puts
his own money in to keep the cafes running and wants that tracked with proof.

WhatsApp is a **bad ledger but a good inbox**. People will always post the
screenshot the second they pay — that habit is not going to change. The job is
to make the app the ledger and demote WhatsApp to a capture channel.

## What the screenshots give us

A UPI receipt screenshot is excellent source material:

- payee name and masked number
- amount
- user message ("2nd installment for painting")
- app transaction ID (e.g. PhonePe `T2609…`)
- **UTR** — unique across all Indian banking → perfect dedup key
- debited account (last digits)

The same screenshot was seen posted in *both* groups. UTR dedup catches that.

## Ways to read a WhatsApp group — ranked

| # | Method | Verdict |
|---|---|---|
| 1 | **Our own share-target app** (this project) | ✅ Primary. Captures intent (category, comment, who paid) at payment time. No Meta dependency. |
| 2 | **WhatsApp Desktop DB on the Mac** (`ChatStorage.sqlite`, unencrypted) | ✅ Reconciler + one-off history backfill. Zero ban risk — reads a file WhatsApp already synced. Needs Mac on + WhatsApp running + media downloaded. Meta-internal schema may shift on updates → pin version, dead-man's switch. |
| 3 | Manual "Export chat with media" zip → upload | ⚠️ Works, but manual and monthly. Fallback only. |
| 4 | Dedicated Android phone + notification listener + Syncthing on media folder | ⚠️ Ban-free, real-time, but caption↔image pairing by timestamp is fuzzy. Keep in pocket. |
| 5 | Official WhatsApp **Groups API** (Meta, 2026) | ⚠️ Exists: business creates group, ≤8 participants, needs Official Business Account (green tick), not on Business-app numbers, cannot attach to existing groups. Apply for OBA in background; not v1. |
| 6 | WhatsApp Cloud API 1:1 "finance number" | ❌ Never sees group messages. Only useful as yet another forward target. |
| 7 | Unofficial linked-device libs (whatsapp-web.js / Baileys) with a burner number | ❌ Last resort. Ban risk; only acceptable because the burner is disposable. |

Decision: **1 + 2**, with 3 as a manual fallback.
