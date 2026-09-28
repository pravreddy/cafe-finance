#!/usr/bin/env python3
"""cafe-finance mail puller — invoices that arrive by email.

Standard library only. Reads a Gmail label (or any IMAP folder) over IMAP,
takes every PDF / image attachment, and posts it to the server's
/api/upload/. The server reads the invoice, dedups it, and puts it in the
review queue; a payment to the same vendor later gets this invoice suggested.

Gmail: create an App Password (Google account → Security → 2-Step → App
passwords), and a label such as "Cafe invoices" with a filter that applies
it to vendor mails. Gmail exposes labels as IMAP folders.

Env (or ~/.cafe-finance/mail.json with the same keys, lower-case):
  IMAP_HOST=imap.gmail.com   IMAP_USER=you@gmail.com   IMAP_PASSWORD=app-password
  IMAP_FOLDER="Cafe invoices"   CF_SERVER=https://ledger.cafegopala.in   CF_TOKEN=...
  CF_CAFE=cafegopala   (optional default cafe slug)

Run from cron on the Hetzner box, or by hand:
  python3 mail/imap_pull.py            # new mail since last run
  python3 mail/imap_pull.py --all      # everything in the folder (first run)
"""
import argparse
import email
import imaplib
import json
import mimetypes
import os
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

STATE_PATH = Path.home() / ".cafe-finance/mail_state.json"
CONFIG_PATH = Path.home() / ".cafe-finance/mail.json"
WANTED = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def cfg(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        return json.loads(CONFIG_PATH.read_text()).get(key.lower(), default)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def post_multipart(url, token, fields, filename, data, ctype):
    boundary = "----cf" + uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
             f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=bytes(body), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "X-Api-Token": token})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode() or "{}")


def hdr(msg, name):
    try:
        return str(make_header(decode_header(msg.get(name, "") or "")))
    except Exception:
        return msg.get(name, "") or ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="scan the whole folder, not just unseen")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    host, user, pw = cfg("IMAP_HOST", "imap.gmail.com"), cfg("IMAP_USER"), cfg("IMAP_PASSWORD")
    folder, server, token = cfg("IMAP_FOLDER", "Cafe invoices"), cfg("CF_SERVER").rstrip("/"), cfg("CF_TOKEN")
    cafe = cfg("CF_CAFE", "")
    if not (user and pw and server and token):
        sys.exit("IMAP_USER, IMAP_PASSWORD, CF_SERVER and CF_TOKEN are required")

    try:
        state = json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"seen": []}
    seen = set(state["seen"])

    M = imaplib.IMAP4_SSL(host)
    M.login(user, pw)
    typ, _ = M.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        sys.exit(f"folder {folder!r} not found — check the label name")
    typ, data = M.search(None, "ALL" if a.all else "UNSEEN")
    ids = data[0].split()
    print(f"{len(ids)} message(s) in {folder!r}")
    sent = 0
    for mid in ids:
        typ, parts = M.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(parts[0][1])
        message_id = (msg.get("Message-ID") or f"<{mid.decode()}@nomsgid>").strip()
        if message_id in seen:
            continue
        sender, subject = hdr(msg, "From"), hdr(msg, "Subject")
        try:
            when = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
        except Exception:
            when = datetime.now(timezone.utc).isoformat()
        n = 0
        for part in msg.walk():
            ctype = part.get_content_type()
            fname = part.get_filename()
            if ctype not in WANTED and not (fname and fname.lower().endswith((".pdf", ".jpg", ".jpeg", ".png"))):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            fname = fname or f"attachment-{n}{mimetypes.guess_extension(ctype) or ''}"
            n += 1
            if a.dry_run:
                print(f"  would send {fname} ({len(payload)} bytes) from {sender!r} — {subject!r}")
                continue
            out = post_multipart(f"{server}/api/upload/", token, {
                "sender": sender, "note": subject, "shared_at": when,
                "origin_ref": message_id, "cafe": cafe,
            }, fname, payload, ctype)
            sent += 1
            print(f"  {fname}: {out.get('doc_type')} {'dup' if out.get('duplicate') else 'new'} — {out.get('message')}")
        if not a.dry_run:
            seen.add(message_id)
            state["seen"] = sorted(seen)[-5000:]
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state))
    M.logout()
    print(f"done: sent={sent}")


if __name__ == "__main__":
    main()
