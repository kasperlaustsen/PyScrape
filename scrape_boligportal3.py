# -*- coding: utf-8 -*-
"""
scrape_boligportal_city.py

- Detail scraper (your current logic)
- City crawler (collect listing URLs for a city)
- Daily updater:
  • loads <city>.csv (if exists)
  • rechecks listings that were active last run
  • finds new ads in the city
  • applies change-tracking (key_1, key_2, ...)
  • writes updated dicts to <city>.csv
"""

import re, csv, os, json, argparse, time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone

# ============ CONFIG ============
HEADERS = {"User-Agent": "bolig-scraper/1.0 (+youremail@example.com)"}
TIMEOUT = 30
SLEEP_BETWEEN_REQUESTS = (0.6, 1.2)  # polite jitter (min, max) seconds
BASE = "https://www.boligportal.dk"
# ================================

# ---------- helpers ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def jitter_sleep(a, b):
    import random
    time.sleep(random.uniform(a, b))

def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()

def get_listing_id(url: str) -> str:
    m = re.search(r"id-(\d+)", url)
    return m.group(1) if m else url

DK_MONTHS = {
    "januar":1,"februar":2,"marts":3,"april":4,"maj":5,"juni":6,
    "juli":7,"august":8,"september":9,"oktober":10,"november":11,"december":12
}

def parse_dk_date(s: str):
    s = (s or "").strip().lower()
    m1 = re.match(r"(\d{1,2})\.\s*([a-zæøå]+)\s+(\d{4})", s)
    if m1:
        d, mn, y = int(m1.group(1)), m1.group(2), int(m1.group(3))
        m = DK_MONTHS.get(mn)
        if m: return datetime(y, m, d).date().isoformat()
    m2 = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m2:
        d, m, y = map(int, m2.groups())
        return datetime(y, m, d).date().isoformat()
    return s

def parse_money(s: str):
    n = re.sub(r"[^\d]", "", s or "")
    return int(n) if n else None

def parse_yes_no(s: str):
    s = (s or "").strip().lower()
    return {"ja": True, "nej": False}.get(s, None)

# ---------- status detector ----------
INACTIVE_SNIPPETS = [
    "udlejet","ikke længere aktiv","annoncen er fjernet",
    "reserveret","annonceringen sættes på pause","denne bolig er ikke længere"
]
def is_active_listing(resp_text: str, status_code: int) -> str:
    if status_code != 200:
        return "inactive"
    txt = re.sub(r"\s+", " ", (resp_text or "").lower())
    if any(snip in txt for snip in INACTIVE_SNIPPETS):
        return "inactive"
    req = ["sagsnr.","ledig fra","lejeperiode","månedlig leje"]
    if sum(lbl in txt for lbl in req) >= 3:
        return "active"
    return "unknown"

# ---------- address extraction ----------
POSTCODE_RE = re.compile(r"\b(\d{4})\b")
def parse_address_text(text: str):
    s = clean_text(text)
    if "," in s:
        left, right = s.split(",", 1)
        street = left.strip()
        right = right.strip()
        m = re.match(r"^(\d{4})\s+(.*)$", right)
        if m:
            return street, m.group(1), m.group(2).strip()
    m = re.search(r"(.*)\s+(\d{4})\s+([A-Za-zÆØÅæøå .\-]+)$", s)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3).strip()
    return None, None, None

def extract_address(soup: BeautifulSoup):
    # A) JSON-LD
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            addr = None
            if isinstance(obj, dict):
                if isinstance(obj.get("address"), dict):
                    addr = obj["address"]
                if not addr:
                    for key in ("offers","item","mainEntity"):
                        sub = obj.get(key)
                        if isinstance(sub, dict) and isinstance(sub.get("address"), dict):
                            addr = sub["address"]; break
                if addr and str(addr.get("@type","")).lower() == "postaladdress":
                    street = addr.get("streetAddress")
                    postcode = addr.get("postalCode")
                    city = addr.get("addressLocality")
                    if street and postcode:
                        return clean_text(street), clean_text(postcode), clean_text(city or "")
    # B) visible text
    candidates = []
    for node in soup.find_all(string=POSTCODE_RE):
        txt = clean_text(str(node))
        if ("," in txt) or re.search(r"\b\d{4}\s+[A-Za-zÆØÅæøå\-]", txt):
            candidates.append(txt)
    comma_first = [c for c in candidates if "," in c]
    for line in comma_first + candidates:
        street, pc, city = parse_address_text(line)
        if pc:
            return street, pc, city
    # C) meta
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        street, pc, city = parse_address_text(meta["content"])
        if pc:
            return street, pc, city
    return None, None, None

# ---------- core parsers ----------
LABELS_ORDER = [
    "Boligtype","Størrelse","Værelser","Etage","Møbleret","Delevenlig","Husdyr tilladt",
    "Elevator","Seniorvenlig","Kun for studerende","Altan/terrasse","Parkering",
    "Opvaskemaskine","Vaskemaskine","Ladestander","Tørretumbler","Energimærke",
    "Lejeperiode","Ledig fra","Månedlig leje","Aconto","Depositum",
    "Forudbetalt husleje","Indflytningspris","Oprettelsesdato","Sagsnr."
]

ENERGY_RE = re.compile(r"^[A-H](\d{4})?$", re.I)
def _is_energy(s: str) -> bool:
    if not s: return False
    cand = s.strip().upper().replace(" ", "")
    return bool(ENERGY_RE.match(cand))

VALUE_VALIDATORS = {
    "Energimærke": _is_energy,
    "Månedlig leje": lambda s: bool(re.search(r"\d", s or "")),
    "Aconto":        lambda s: bool(re.search(r"\d", s or "")),
    "Depositum":     lambda s: bool(re.search(r"\d", s or "")),
    "Forudbetalt husleje": lambda s: bool(re.search(r"\d", s or "")),
    "Indflytningspris":    lambda s: bool(re.search(r"\d", s or "")),
    "Størrelse":     lambda s: bool(re.search(r"\d", s or "")),
    "Værelser":      lambda s: bool(re.search(r"\d", s or "")),
}

def extract_pairs_semantic(soup):
    pairs = {}
    def harvest_section(h2_text):
        h2 = soup.find(lambda t: t.name in ("h2","h3") and h2_text in t.get_text(strip=True))
        if not h2: return
        section = h2.find_next()
        if not section: return
        for dt in section.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                k = clean_text(dt.get_text())
                v = clean_text(dd.get_text(" "))
                pairs[k] = v
        for row in section.find_all(True, recursive=True):
            kids = [k for k in row.children if getattr(k, "get_text", None)]
            if len(kids) == 2:
                k = clean_text(kids[0].get_text()); v = clean_text(kids[1].get_text(" "))
                if k and v and k in LABELS_ORDER and k not in pairs:
                    pairs[k] = v
    harvest_section("Detaljer om bolig")
    harvest_section("Detaljer om udlejning")
    return pairs

def extract_pairs_by_lines(soup):
    text = soup.get_text("\n")
    lines = [clean_text(x) for x in text.split("\n")]
    lines = [x for x in lines if x]
    pairs = {}
    wanted = set(LABELS_ORDER)
    header_re = re.compile(r"^Detaljer om (bolig|udlejning)$", re.I)
    n = len(lines); i = 0
    while i < n:
        line = lines[i]
        if line in wanted and line not in pairs:
            validator = VALUE_VALIDATORS.get(line)
            j = i + 1; steps = 0; MAX_LOOKAHEAD = 6
            while j < n and steps < MAX_LOOKAHEAD:
                cand = lines[j]
                if cand in wanted or header_re.match(cand):
                    break
                if cand and ((validator is None) or validator(cand)):
                    pairs[line] = cand; i = j; break
                j += 1; steps += 1
        i += 1
    return pairs

def normalize(data):
    out = {}
    for k, v in data.items():
        if k in {"Møbleret","Delevenlig","Husdyr tilladt","Elevator","Seniorvenlig",
                 "Kun for studerende","Altan/terrasse","Parkering","Opvaskemaskine",
                 "Vaskemaskine","Ladestander","Tørretumbler"}:
            out[k] = parse_yes_no(v)
        elif k in {"Månedlig leje","Aconto","Depositum","Forudbetalt husleje","Indflytningspris"}:
            out[k] = parse_money(v)
        elif k in {"Ledig fra","Oprettelsesdato"}:
            out[k] = parse_dk_date(v)
        elif k == "Størrelse":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v or "") else None
        elif k == "Værelser":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v or "") else None
        elif k == "Etage":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v or "") else v
        elif k == "Sagsnr.":
            out[k] = re.sub(r"[^\d]", "", v or "") or v
        elif k == "Energimærke":
            if v:
                cand = v.strip().upper().replace(" ", "")
                if _is_energy(cand):
                    out[k] = cand
                else:
                    # keep key but set None if weird
                    out[k] = None
            else:
                out[k] = None
        else:
            out[k] = v
    return out

# ---------- detail scraping ----------
def scrape_listing(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    status = is_active_listing(r.text, r.status_code)
    soup = BeautifulSoup(r.text, "lxml")
    pairs = extract_pairs_semantic(soup) or extract_pairs_by_lines(soup)
    ordered = {k: pairs.get(k) for k in LABELS_ORDER if k in pairs}
    data = normalize(ordered)

    # energy fallback (ok if remains None)
    if data.get("Energimærke") is None:
        full_text = clean_text(soup.get_text(" "))
        m = re.search(r"\bEnergimærke\b[:\s]*([A-H](?:\d{4})?)\b", full_text, flags=re.I)
        if m:
            data["Energimærke"] = m.group(1).upper()

    data["url"] = url
    data["listing_id"] = get_listing_id(url)
    data["status"] = status
    data["scraped_at"] = now_iso()

    street, postcode, city = extract_address(soup)
    # trim floor tail like " - 3. sal"
    if city:
        parts = [p.strip() for p in city.split(" - ", 1)]
        if len(parts) == 2 and re.search(r"^(?:\d+\.?\s*sal|st\.?|stue|kld\.?|kælder)$", parts[1], flags=re.I):
            city = parts[0]
    data["street"] = street
    data["postcode"] = postcode
    data["city"] = city

    return data

# ---------- city search (collect listing URLs) ----------
def city_slug(city: str) -> str:
    # very simple normalization for the URL path
    return city.strip().lower()

def find_city_urls(city: str, max_pages=5):
    """
    Crawl search pages for the city and return listing detail URLs (unique).
    Works by scanning anchors that contain '/id-<digits>'.
    """
    urls = []
    seen = set()
    slug = city_slug(city)
    # Typical category: apartments = 'lejligheder'; you can add others later
    page = 1
    while page <= max_pages:
        # Try both with and without trailing slash robustness
        search_url = f"{BASE}/lejligheder/{slug}/?page={page}"
        r = requests.get(search_url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            break
        soup = BeautifulSoup(r.text, "lxml")
        found = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/id-" in href:
                # normalize absolute
                full = href if href.startswith("http") else (BASE + href)
                if full not in seen:
                    seen.add(full); urls.append(full); found += 1
        if found == 0:
            # likely no more results
            break
        page += 1
        jitter_sleep(*SLEEP_BETWEEN_REQUESTS)
    return urls

# ---------- change tracking (key_<n>) ----------
IGNORED_KEYS_FOR_CHANGE = {"listing_id","url","status","scraped_at"}
def _max_suffix_index(snapshot: dict, key: str) -> int:
    pat = re.compile(rf"^{re.escape(key)}_(\d+)$")
    max_i = 0
    for k in snapshot.keys():
        m = pat.match(k)
        if m:
            max_i = max(max_i, int(m.group(1)))
    return max_i

def add_change_suffixes(prev_snapshot: dict, curr_snapshot: dict) -> dict:
    if not prev_snapshot:
        return dict(curr_snapshot)
    out = dict(curr_snapshot)
    for key, curr_val in curr_snapshot.items():
        if key in IGNORED_KEYS_FOR_CHANGE:
            continue
        prev_val = prev_snapshot.get(key)
        if prev_val is None:
            continue
        if curr_val != prev_val:
            next_i = _max_suffix_index(prev_snapshot, key) + 1
            out[f"{key}_{next_i}"] = curr_val
    return out

# ---------- CSV I/O ----------
def read_city_csv(path: str) -> dict:
    """
    Load existing CSV into a dict keyed by listing_id -> snapshot(dict).
    Returns {} if file does not exist.
    """
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # turn "" into None for convenience
            snap = {k: (v if v != "" else None) for k, v in row.items()}
            lid = snap.get("listing_id")
            if lid:
                out[lid] = snap
    return out

def write_city_csv(path: str, snapshots: list[dict]):
    # union of all keys, stable-ish order with some preferred keys first
    fieldnames = set()
    for s in snapshots:
        fieldnames.update(s.keys())
    preferred = ["listing_id","url","status","scraped_at","Boligtype","Størrelse","Værelser","Etage",
                 "Månedlig leje","Aconto","Depositum","Forudbetalt husleje","Indflytningspris",
                 "Lejeperiode","Ledig fra","Oprettelsesdato","Energimærke",
                 "street","postcode","city","Sagsnr."]
    # Place preferred first, then the rest sorted
    rest = sorted(fn for fn in fieldnames if fn not in preferred)
    header = preferred + rest
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        w.writeheader()
        for s in snapshots:
            # ensure all missing keys are present as empty
            row = {k: s.get(k, "") for k in header}
            w.writerow(row)

# ---------- daily updater ----------
def daily_update_city(city: str, max_pages=5, csv_dir="."):
    """
    1) Load previous CSV (<city>.csv) if present
    2) Determine 'active last run' listing_ids
    3) Re-scrape those
    4) Crawl city search for new URLs and scrape those not seen before
    5) Apply change suffixes (key_1, key_2, ...)
    6) Save merged latest snapshots to <city>.csv
    """
    csv_path = os.path.join(csv_dir, f"{city}.csv")
    prev_by_id = read_city_csv(csv_path)

    # (1) ids that were active last run
    active_ids = [lid for lid, snap in prev_by_id.items() if (snap.get("status") == "active")]

    # (2) recheck active ones first
    latest_by_id = {}
    for lid in active_ids:
        url = prev_by_id[lid].get("url")
        if not url:
            continue
        try:
            latest = scrape_listing(url)
            latest = add_change_suffixes(prev_by_id.get(lid), latest)
            latest_by_id[lid] = latest
            jitter_sleep(*SLEEP_BETWEEN_REQUESTS)
        except Exception as e:
            # keep previous snapshot if request fails
            latest_by_id[lid] = prev_by_id[lid]

    # (3) discover current URLs in the city
    city_urls = find_city_urls(city, max_pages=max_pages)

    # (4) add new URLs (not in prev)
    for url in city_urls:
        lid = get_listing_id(url)
        if lid in latest_by_id or lid in prev_by_id:
            continue
        try:
            latest = scrape_listing(url)
            latest_by_id[lid] = latest  # first snapshot; no _n keys yet
            jitter_sleep(*SLEEP_BETWEEN_REQUESTS)
        except Exception:
            pass

    # (5) carry over previously inactive/unknown ones (to keep them in DB)
    for lid, snap in prev_by_id.items():
        if lid not in latest_by_id:
            # keep previous snapshot (so we don't lose historic ads)
            latest_by_id[lid] = snap

    # (6) write CSV
    snapshots = list(latest_by_id.values())
    write_city_csv(csv_path, snapshots)
    print(f"[daily] {city}: wrote {len(snapshots)} rows to {csv_path}")

# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser(description="BoligPortal city scraper & daily updater")
    sub = parser.add_subparsers(dest="cmd")

    p_daily = sub.add_parser("daily", help="Run daily update for a city")
    p_daily.add_argument("--city", required=True, help="City name, e.g., Horsens")
    p_daily.add_argument("--pages", type=int, default=5, help="Max search pages to crawl")
    p_daily.add_argument("--csv-dir", default=".", help="Folder to store <city>.csv")

    p_once = sub.add_parser("scrape-url", help="Scrape a single listing URL")
    p_once.add_argument("--url", required=True)

    args = parser.parse_args()

    if args.cmd == "scrape-url":
        d = scrape_listing(args.url)
        for k, v in d.items():
            print(f"{k}: {v}")
    else:
        # default command = daily
        if not args.cmd:
            print("No command given. Use: daily --city Horsens")
            return
        daily_update_city(args.city, max_pages=args.pages, csv_dir=args.csv_dir)

if __name__ == "__main__":
    main()
