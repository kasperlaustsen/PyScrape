# -*- coding: utf-8 -*-
"""
Created on Thu Aug 21 20:15:48 2025

@author: KALSE
"""

#!/usr/bin/env python3
import os, re, sqlite3, json
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

# ================== CONFIG ==================
URLS = [
    "https://www.boligportal.dk/lejligheder/horsens/130m2-4-vaer-id-4962343"
]
HEADERS = {"User-Agent": "bolig-checker/1.0 (+your@email)"}
DB_PATH = os.environ.get("BP_DB_PATH", "bolig_checks.sqlite3")
TIMEOUT = 30
# ============================================

# ---------- status detector (from earlier) ----------
INACTIVE_SNIPPETS = [
    "udlejet", "ikke længere aktiv", "annoncen er fjernet",
    "reserveret", "annonceringen sættes på pause",
    "denne bolig er ikke længere"
]

def is_active_listing(resp_text: str, status_code: int) -> str:
    if status_code != 200:
        return "inactive"
    text = re.sub(r"\s+", " ", resp_text.lower())
    if any(snippet in text for snippet in INACTIVE_SNIPPETS):
        return "inactive"
    required_labels = ["sagsnr.", "ledig fra", "lejeperiode", "månedlig leje"]
    positives = sum(lbl in text for lbl in required_labels)
    if positives >= 3:
        return "active"
    return "unknown"

# ---------- small helpers ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_listing_id(url: str) -> str:
    # Prefer numeric id in URL like "...id-4962343"
    m = re.search(r"id-(\d+)", url)
    return m.group(1) if m else url

# ---------- DB setup ----------
DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS listings (
  listing_id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  last_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  status TEXT NOT NULL,
  raw_http INTEGER,
  FOREIGN KEY(listing_id) REFERENCES listings(listing_id)
);
CREATE TABLE IF NOT EXISTS rental_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  listing_id TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  prev_status TEXT NOT NULL,
  new_status TEXT NOT NULL
);
"""

def ensure_db(conn):
    cur = conn.cursor()
    for stmt in DDL.strip().split(";"):
        s = stmt.strip()
        if s:
            cur.execute(s)
    conn.commit()

# ---------- core check ----------
def check_once(url: str, session: requests.Session):
    listing_id = get_listing_id(url)
    try:
        r = session.get(url, headers=HEADERS, timeout=TIMEOUT)
        status = is_active_listing(r.text, r.status_code)
        http_code = r.status_code
    except Exception as e:
        status = "unknown"
        http_code = None

    return {
        "listing_id": listing_id,
        "url": url,
        "status": status,
        "http_code": http_code,
        "checked_at": now_iso(),
    }

def upsert_and_detect(conn, record):
    """
    Save the check and detect transitions.
    Returns (changed: bool, prev_status: str|None)
    """
    cur = conn.cursor()
    # Insert history row
    cur.execute(
        "INSERT INTO status_history(listing_id, checked_at, status, raw_http) VALUES (?, ?, ?, ?)",
        (record["listing_id"], record["checked_at"], record["status"], record["http_code"])
    )

    # Upsert into listings
    cur.execute("SELECT last_status FROM listings WHERE listing_id = ?", (record["listing_id"],))
    row = cur.fetchone()
    prev_status = row[0] if row else None

    if row is None:
        cur.execute(
            "INSERT INTO listings(listing_id, url, first_seen, last_seen, last_status) VALUES (?, ?, ?, ?, ?)",
            (record["listing_id"], record["url"], record["checked_at"], record["checked_at"], record["status"])
        )
        changed = False
    else:
        changed = (prev_status != record["status"])
        cur.execute(
            "UPDATE listings SET last_seen = ?, last_status = ?, url = ? WHERE listing_id = ?",
            (record["checked_at"], record["status"], record["url"], record["listing_id"])
        )

    # Record a “rented event” when active → inactive
    if prev_status == "active" and record["status"] == "inactive":
        cur.execute(
            "INSERT INTO rental_events(listing_id, changed_at, prev_status, new_status) VALUES (?, ?, ?, ?)",
            (record["listing_id"], record["checked_at"], prev_status, record["status"])
        )

    conn.commit()
    return changed, prev_status

def main():
    conn = sqlite3.connect(DB_PATH)
    ensure_db(conn)

    session = requests.Session()
    for url in URLS:
        rec = check_once(url, session)
        changed, prev = upsert_and_detect(conn, rec)

        # One-line log output (great for cron logs)
        print(json.dumps({
            "url": rec["url"],
            "listing_id": rec["listing_id"],
            "status": rec["status"],
            "prev_status": prev,
            "changed": changed,
            "http": rec["http_code"],
            "checked_at": rec["checked_at"]
        }))

    conn.close()

if __name__ == "__main__":
    main()
