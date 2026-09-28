# 02 — Capture app ("Cafe Finance" in the share sheet)

## What it is

An Android **share target**: it appears in the system share sheet next to
WhatsApp, Gmail, Drive. Not a messenger. One job: take an image (+ optional
text), get the facts out of it, let a human add meaning, save, forward.

## Two flows

### A. Pay first (preferred — one share, both places)

1. Pay in PhonePe / GPay → Share receipt (or screenshot → Share)
2. Pick **Cafe Finance** instead of WhatsApp
3. App opens with the image loaded; vision OCR pre-fills amount, payee, UTR, date, app txn ID
4. User picks cafe + category chip, types/edits comment, confirms who paid (defaults to self)
5. **Save** → uploads to server (photo + fields, status = pending review)
6. App immediately calls the system share with the same image + caption
   pre-filled → user picks the WhatsApp group → sent. Group sees exactly what it sees today.

Net cost to the user: one extra screen. Same number of taps.

### B. Already in WhatsApp (catch-up)

Any image already in a group (posted from WhatsApp Web, by someone without the
app, or forgotten) → long-press → Share → **Cafe Finance** → same review screen.
Works from phone WhatsApp and from WhatsApp Web via "Download → share".

## Tech choice

**v1: PWA with Web Share Target** (`share_target` in `manifest.json`).
- Served from the Hetzner box (e.g. `https://ledger.cafegopala.in`), "Add to Home Screen"
- Chrome on Android lists installed PWAs in the share sheet; receives files via POST
- Forward-to-WhatsApp via `navigator.share({ files, text })` (Android Chrome supports files)
- No APK, no Play Store, updates instantly. Everyone is on Android.
- Limitation: iOS Safari has no share-target for PWAs.

**v2 (if needed): Capacitor** — same UI wrapped like FastPOS, share-target plugin
+ intent filter, sideloaded APK; adds iOS share extension later.

## Screens (keep it to three)

1. **Receive/Review** — image on top, extracted fields below, category chips,
   comment box, cafe selector, paid-by selector, Save & Forward button
2. **My entries** — list of what I have posted, status (pending / confirmed / rejected)
3. **Sign-in** — phone number + OTP or a per-person link; 3–5 users only, keep it simple

## OCR / extraction

- Server-side, not on the phone. Phone uploads image → server calls a vision
  model → returns JSON `{amount, payee, utr, txn_id, date, message, app, confidence}`
- Provider pluggable like the rest of the stack: Claude vision by default,
  Ollama vision (llava / qwen-vl) as explicit opt-in (slow on CPU — same rule as llama3.1)
- Cost at cafe volume is negligible.

## Caption forwarded to WhatsApp

Template, so the group post itself becomes machine-friendly for the reconciler:

    ₹10,000 · Painting/repairs · 2nd installment for painting · #CF-000123

The `#CF-…` tag lets the reconciler match a group post to a ledger row even
before the UTR is extracted from the image.
