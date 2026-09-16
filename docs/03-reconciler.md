# 03 — Reconciler (WhatsApp Desktop database on the Mac)

## Why it exists

The app is the front door. The reconciler is the audit: it proves that
**everything posted to the groups is in the ledger**, and it backfills history
before the app existed. It also covers the days someone skips the app.

## Source

WhatsApp for Mac keeps chat history as a plain Core Data SQLite store:

    ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite

Media that has been downloaded lives under `Message/Media/` in the same container.
Readable by any process running as the user; the reading process (Terminal /
launchd script) needs **Full Disk Access** in System Settings.

Tables of interest (verify against the installed version before relying on them):
`ZWACHATSESSION` (chats — filter by group name / JID), `ZWAMESSAGE`
(`ZMESSAGEDATE`, sender, text, type), `ZWAMEDIAITEM` (`ZMEDIALOCALPATH`, caption).

## Behaviour

- Runs on launchd every 5 min (or weekly once the app is trusted) — never manual
- Keeps a cursor (last `Z_PK` / date) per group
- For each new group message: sender, timestamp, caption, media local path
- Copies media not yet seen; POSTs to server `/reconcile/` with the image + metadata
- Server matches by (a) `#CF-…` tag in caption, (b) UTR after extraction,
  (c) amount + date ± 1 day + payee as a weak match → creates a **"seen in group,
  not in ledger"** item in the review queue, or marks an existing ledger row as
  *confirmed in group*
- Copy `ChatStorage.sqlite` to a temp file before querying (WAL / open handle safety)

## Known limits

- Mac must be on with WhatsApp running; media must be downloaded → set media
  auto-download on; flag "image not downloaded" rows so someone opens the chat once
- Schema is Meta-internal; may change on WhatsApp updates → pin WhatsApp version
  in the runbook, and put the job on an Uptime Kuma **dead-man's switch** so a silent
  break is noticed within a day
- A spare Mac mini / old MacBook turns this into an appliance; otherwise it runs
  when Praveen's laptop is open, which is fine for a weekly audit

## One-off backfill

First run walks the full history of both groups and creates ledger rows for
every image with a UTR (status = *imported, needs review*). That is how the
old months get into the books without anyone re-posting anything.
