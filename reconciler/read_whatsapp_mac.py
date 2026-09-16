#!/usr/bin/env python3
"""cafe-finance reconciler — read the WhatsApp Desktop database on a Mac.

Standard library only; nothing to install on the Mac.

WhatsApp for Mac keeps its synced history as a plain Core Data SQLite store:
    ~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite
and downloaded media under Message/Media/ in the same container. We copy the
DB to a temp file (never touch the live one), read the finance groups, and
post each message (+ image when it is on disk) to the server's /api/reconcile/.
The server does the matching; this script just delivers what the group saw.

Modes
  --probe                 confirm the schema, list group chats  (run this FIRST)
  --dump "CG fin" [-n 20] print the last N messages of a group with media paths
  --push                  send new messages of the configured groups to the server
  --push --backfill       same, but walk full history and mark rows imported

Config: ~/.cafe-finance/config.json
  {
    "server": "https://ledger.guhya.co.in",
    "token": "the API_TOKEN from the server .env",
    "groups": ["CG fin", "Krishna Kitchen Finance"],
    "heartbeat_url": "https://uptime.../api/push/xxxx?status=up"   (optional)
  }

The reading process needs Full Disk Access (System Settings → Privacy &
Security → Full Disk Access → add Terminal / python3 / the launchd runner).
"""
import argparse
import json
import mimetypes
import os
import shutil
import sqlite3
import sys
import tempfile
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONTAINER = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
DB_PATH = CONTAINER / "ChatStorage.sqlite"
CONFIG_PATH = Path.home() / ".cafe-finance/config.json"
STATE_PATH = Path.home() / ".cafe-finance/state.json"
CORE_DATA_EPOCH = 978307200  # 2001-01-01 in unix seconds
IST = timezone(timedelta(hours=5, minutes=30))

# Message types worth sending. 1 = image, 8 = document (PDF invoices). Text-only
# rows are sent too (cheap) so "#CF-000123 sent" confirmations can be matched.
MEDIA_TYPES = {1: "image", 8: "document"}


# ------------------------------------------------------------------ db helpers

def snapshot_db():
    """Copy the DB (+ WAL/SHM if present) so we never hold the live file open."""
    if not DB_PATH.exists():
        sys.exit(f"WhatsApp database not found at {DB_PATH}. Is WhatsApp Desktop installed and signed in?")
    tmp = Path(tempfile.mkdtemp(prefix="cf-wa-"))
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB_PATH) + suffix)
        if src.exists():
            try:
                shutil.copy2(src, tmp / src.name)
            except PermissionError:
                sys.exit("Permission denied reading ChatStorage.sqlite — grant Full Disk Access to the "
                         "program running this script (System Settings → Privacy & Security).")
    return sqlite3.connect(tmp / DB_PATH.name), tmp


def columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def has(con, table, col):
    return col in columns(con, table)


def cd_to_dt(v):
    if v is None:
        return None
    return datetime.fromtimestamp(float(v) + CORE_DATA_EPOCH, tz=timezone.utc).astimezone(IST)


# ------------------------------------------------------------------ queries

def group_sessions(con):
    cols = columns(con, "ZWACHATSESSION")
    type_col = "ZSESSIONTYPE" if "ZSESSIONTYPE" in cols else None
    sql = "SELECT Z_PK, ZPARTNERNAME, ZCONTACTJID" + (f", {type_col}" if type_col else ", NULL") + " FROM ZWACHATSESSION"
    out = []
    for pk, name, jid, stype in con.execute(sql):
        is_group = (stype == 1) if stype is not None else str(jid or "").endswith("@g.us")
        if is_group:
            out.append({"pk": pk, "name": name or "", "jid": jid or ""})
    return out


def session_by_name(con, name):
    for s in group_sessions(con):
        if s["name"].strip().lower() == name.strip().lower():
            return s
    return None


def member_names(con, session_pk):
    """jid -> display name for group members (falls back to push names)."""
    names = {}
    try:
        for jid, cname in con.execute(
                "SELECT ZMEMBERJID, ZCONTACTNAME FROM ZWAGROUPMEMBER WHERE ZCHATSESSION=?", (session_pk,)):
            if jid:
                names[jid] = cname or ""
    except sqlite3.OperationalError:
        pass
    try:
        for jid, push in con.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
            if jid and not names.get(jid):
                names[jid] = push or ""
    except sqlite3.OperationalError:
        pass
    return names


def messages(con, session_pk, after_pk=0, limit=None):
    mcols = columns(con, "ZWAMESSAGE")
    stanza = "m.ZSTANZAID" if "ZSTANZAID" in mcols else "NULL"
    gm = "m.ZGROUPMEMBER" if "ZGROUPMEMBER" in mcols else "NULL"
    sql = f"""
      SELECT m.Z_PK, {stanza}, m.ZMESSAGEDATE, m.ZISFROMME, m.ZFROMJID, m.ZMESSAGETYPE, m.ZTEXT,
             mi.ZMEDIALOCALPATH, mi.ZTITLE, mi.ZVCARDSTRING, {gm}
        FROM ZWAMESSAGE m
        LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK
       WHERE m.ZCHATSESSION = ? AND m.Z_PK > ?
       ORDER BY m.Z_PK ASC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = []
    for (pk, stanza_id, mdate, from_me, from_jid, mtype, text, mpath, mtitle, mmime, gmember) in con.execute(sql, (session_pk, after_pk)):
        rows.append({
            "pk": pk,
            "id": stanza_id or f"pk{pk}",
            "sent_at": cd_to_dt(mdate),
            "from_me": bool(from_me),
            "from_jid": from_jid or "",
            "type": mtype,
            "text": text or "",
            "media_path": mpath or "",
            "caption": mtitle or "",
            "media_mime": mmime or "",
            "group_member": gmember,
        })
    return rows


def member_jid_for(con, gmember_pk):
    if not gmember_pk:
        return ""
    try:
        r = con.execute("SELECT ZMEMBERJID FROM ZWAGROUPMEMBER WHERE Z_PK=?", (gmember_pk,)).fetchone()
        return (r[0] or "") if r else ""
    except sqlite3.OperationalError:
        return ""


def sender_label(con, row, names):
    if row["from_me"]:
        return "You", ""
    jid = row["from_jid"] or member_jid_for(con, row["group_member"])
    return (names.get(jid) or jid.split("@")[0] or "?"), jid


# ------------------------------------------------------------------ http

def post_multipart(url, token, fields, file_path=None):
    boundary = "----cf" + uuid.uuid4().hex
    body = bytearray()
    for k, v in fields.items():
        if v is None:
            continue
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    if file_path:
        p = Path(file_path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode()
        body += p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=bytes(body), method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Api-Token": token,
    })
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode() or "{}")


def get_json(url, token):
    req = urllib.request.Request(url, headers={"X-Api-Token": token})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


# ------------------------------------------------------------------ modes

def cmd_probe(con):
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ZWA%' ORDER BY name")]
    print("ZWA* tables:", ", ".join(tables))
    for t in ("ZWACHATSESSION", "ZWAMESSAGE", "ZWAMEDIAITEM", "ZWAGROUPMEMBER"):
        if t in tables:
            print(f"\n{t}: {', '.join(columns(con, t))}")
        else:
            print(f"\n{t}: MISSING — schema changed, update this script")
    print("\nGroup chats:")
    for s in group_sessions(con):
        n = con.execute("SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZCHATSESSION=?", (s["pk"],)).fetchone()[0]
        print(f"  [{s['pk']:>5}] {s['name']!r:45} {n:>6} msgs  {s['jid']}")
    print(f"\nMedia base: {CONTAINER / 'Message/Media'}  exists={ (CONTAINER / 'Message/Media').exists() }")


def cmd_dump(con, group, n):
    s = session_by_name(con, group)
    if not s:
        sys.exit(f"No group named {group!r}. Run --probe to see names.")
    names = member_names(con, s["pk"])
    rows = messages(con, s["pk"])[-n:]
    for r in rows:
        who, _ = sender_label(con, r, names)
        kind = MEDIA_TYPES.get(r["type"], "text" if r["type"] == 0 else f"type{r['type']}")
        media = ""
        if r["media_path"]:
            full = CONTAINER / r["media_path"]
            media = f"  media={r['media_path']} ({'on disk' if full.exists() else 'NOT DOWNLOADED'})"
        text = (r["text"] or r["caption"]).replace("\n", " ")[:80]
        print(f"{r['sent_at']:%Y-%m-%d %H:%M}  {who:<20} {kind:<8} {text}{media}")


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit(f"Missing {CONFIG_PATH} — see the docstring for the format.")
    return json.loads(CONFIG_PATH.read_text())


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"cursor": {}}


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def cmd_push(con, backfill: bool, dry: bool):
    cfg, state = load_config(), load_state()
    server, token = cfg["server"].rstrip("/"), cfg["token"]
    sent = skipped = errors = 0
    for group in cfg.get("groups", []):
        s = session_by_name(con, group)
        if not s:
            print(f"!! group {group!r} not found in WhatsApp — skipping"); continue
        names = member_names(con, s["pk"])
        after = 0 if backfill else int(state["cursor"].get(group, 0))
        rows = messages(con, s["pk"], after_pk=after)
        print(f"{group}: {len(rows)} message(s) after pk {after}")
        for r in rows:
            if r["type"] not in MEDIA_TYPES and not (r["text"] or "").strip():
                state["cursor"][group] = r["pk"]; continue     # stickers, calls, etc.
            who, jid = sender_label(con, r, names)
            file_path = None
            if r["media_path"]:
                full = CONTAINER / r["media_path"]
                if full.exists():
                    file_path = full
                else:
                    print(f"   pk {r['pk']}: media not downloaded ({r['media_path']}) — open the chat in WhatsApp once")
            fields = {
                "group_name": group, "wa_message_pk": r["id"], "sender": who, "sender_jid": jid,
                "sent_at": r["sent_at"].isoformat() if r["sent_at"] else "",
                "caption": r["text"] or r["caption"], "media_path": r["media_path"],
                "imported": "1" if backfill else "0",
            }
            if dry:
                print(f"   would send pk {r['pk']} {who} {fields['caption'][:50]!r} file={bool(file_path)}")
                continue
            try:
                out = post_multipart(f"{server}/api/reconcile/", token, fields, file_path)
                sent += 1
                if out.get("doc_type") == "payment":
                    print(f"   pk {r['pk']}: {out.get('cf_tag')} {'dup' if out.get('duplicate') else 'NEW'} ({out.get('match_method')})")
                state["cursor"][group] = r["pk"]
                save_state(state)
            except Exception as e:  # keep going; the server dedups, we retry next run
                errors += 1
                print(f"   pk {r['pk']}: ERROR {e}")
                break
    print(f"done: sent={sent} errors={errors}")
    hb = cfg.get("heartbeat_url")
    if hb and not dry and errors == 0:
        try:
            urllib.request.urlopen(hb, timeout=20).read()
        except Exception as e:
            print(f"heartbeat failed: {e}")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--dump", metavar="GROUP")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    con, tmp = snapshot_db()
    try:
        if a.probe:
            cmd_probe(con)
        elif a.dump:
            cmd_dump(con, a.dump, a.n)
        elif a.push:
            return cmd_push(con, a.backfill, a.dry_run)
        else:
            ap.print_help()
    finally:
        con.close()
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
