# -*- coding: utf-8 -*-
"""
Created on Thu Aug 21 19:44:48 2025

@author: KALSE
"""

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from datetime import datetime, timezone
import json


URL = "https://www.boligportal.dk/lejligheder/horsens/130m2-4-vaer-id-4962343"
HEADERS = {"User-Agent": "research-bot/1.0 (+contact@example.com)"}
TIMEOUT = 30   # timeout in seconds for HTTP requests

# --- helpers ---------------------------------------------------------------

POSTCODE_RE = re.compile(r"\b(\d{4})\b")
ENERGY_RE = re.compile(r"^[A-H](\d{4})?$", re.I)

def _is_energy(s: str) -> bool:
    if not s:
        return False
    cand = s.strip().upper().replace(" ", "")
    return bool(ENERGY_RE.match(cand))

VALUE_VALIDATORS = {
    # accept only A–H or A2010/A2018 etc.
    "Energimærke": _is_energy,
    # simple sanity checks to reduce mis-pairing:
    "Månedlig leje": lambda s: bool(re.search(r"\d", s or "")),
    "Aconto":        lambda s: bool(re.search(r"\d", s or "")),
    "Depositum":     lambda s: bool(re.search(r"\d", s or "")),
    "Forudbetalt husleje": lambda s: bool(re.search(r"\d", s or "")),
    "Indflytningspris":    lambda s: bool(re.search(r"\d", s or "")),
    "Størrelse":     lambda s: bool(re.search(r"\d", s or "")),
    "Værelser":      lambda s: bool(re.search(r"\d", s or "")),
    # others default to “no special validation”
}
    
def parse_address_text(text: str):
    """
    Expected forms:
      'Nørregade 15, 8700 Horsens'
      'Nørregade 15  ,  8700 Horsens'
    Returns (street, postcode, city) or (None,None,None) if not parseable.
    """
    s = clean_text(text)
    # Prefer a comma split if present
    if "," in s:
        left, right = s.split(",", 1)
        street = left.strip()
        right = right.strip()
        m = re.match(r"^(\d{4})\s+(.*)$", right)
        if m:
            return street, m.group(1), m.group(2).strip()
    # No comma: try to find 'NNNN City' at the end
    m = re.search(r"(.*)\s+(\d{4})\s+([A-Za-zÆØÅæøå .\-]+)$", s)
    if m:
        return m.group(1).strip(), m.group(2), m.group(3).strip()
    return None, None, None

def extract_address(soup: BeautifulSoup):
    """
    Try multiple strategies to get (street, postcode, city).
    """
    # A) JSON-LD with PostalAddress
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        # JSON-LD can be an object or a list
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            addr = None
            # Common patterns: 'address' field with '@type': 'PostalAddress'
            if isinstance(obj, dict):
                if "address" in obj and isinstance(obj["address"], dict):
                    addr = obj["address"]
                # Sometimes nested under 'offers' or 'item'
                if not addr:
                    for key in ("offers", "item", "mainEntity"):
                        sub = obj.get(key)
                        if isinstance(sub, dict) and isinstance(sub.get("address"), dict):
                            addr = sub["address"]
                            break
                if addr and addr.get("@type", "").lower() == "postaladdress":
                    street = addr.get("streetAddress")
                    postcode = addr.get("postalCode")
                    city = addr.get("addressLocality")
                    if street and postcode:
                        return clean_text(street), clean_text(postcode), clean_text(city or "")
    # B) Visible text: search for elements that contain a 4-digit postcode
    candidates = []
    for node in soup.find_all(string=POSTCODE_RE):
        txt = clean_text(str(node))
        # Heuristic: likely address lines are short-ish and contain either a comma
        # or 'NNNN City' structure
        if ("," in txt) or re.search(r"\b\d{4}\s+[A-Za-zÆØÅæøå\-]", txt):
            candidates.append(txt)
    # Prefer the first good parse with a comma, otherwise any good parse
    comma_first = [c for c in candidates if "," in c]
    for line in comma_first + candidates:
        street, pc, city = parse_address_text(line)
        if pc:
            return street, pc, city
    # C) Meta tags (rare, but cheap to try)
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        street, pc, city = parse_address_text(meta["content"])
        if pc:
            return street, pc, city
    return None, None, None

def now_iso():
    """Return current UTC time as ISO string"""
    return datetime.now(timezone.utc).isoformat()

def get_listing_id(url: str) -> str:
    # Prefer numeric id in URL like "...id-4962343"
    m = re.search(r"id-(\d+)", url)
    return m.group(1) if m else url


DK_MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12
}

def parse_dk_date(s: str):
    s = s.strip().lower()
    # formats: "1. september 2025" or "12.8.2025"
    m1 = re.match(r"(\d{1,2})\.\s*([a-zæøå]+)\s+(\d{4})", s)
    if m1:
        d, month_name, y = int(m1.group(1)), m1.group(2), int(m1.group(3))
        m = DK_MONTHS.get(month_name)
        if m:
            return datetime(y, m, d).date().isoformat()
    m2 = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if m2:
        d, m, y = map(int, m2.groups())
        return datetime(y, m, d).date().isoformat()
    return s  # fallback: return original if unexpected

def parse_money(s: str):
    # "9.695 kr.", "29.085 kr.", "0 kr.", "800 kr."
    n = re.sub(r"[^\d]", "", s)
    return int(n) if n else None

def parse_yes_no(s: str):
    s = s.strip().lower()
    return {"ja": True, "nej": False}.get(s, None)

def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip()

# --- status detector helpers -----------------------------------------------

INACTIVE_SNIPPETS = [
    "udlejet", "ikke længere aktiv", "annoncen er fjernet",
    "reserveret", "annonceringen sættes på pause",
    "denne bolig er ikke længere"
]

def is_active_listing(resp_text: str, status_code: int) -> str:
    # 1) HTTP layer
    if status_code != 200:
        return "inactive"  # removed from public view

    text = re.sub(r"\s+", " ", resp_text.lower())

    # 2) explicit negative cues
    if any(snippet in text for snippet in INACTIVE_SNIPPETS):
        return "inactive"

    # 3) positive structure cues
    required_labels = ["sagsnr.", "ledig fra", "lejeperiode", "månedlig leje"]
    positives = sum(lbl in text for lbl in required_labels)

    if positives >= 3:
        return "active"

    return "unknown"


# --- core parsers ----------------------------------------------------------

LABELS_ORDER = [
    # bolig
    "Boligtype","Størrelse","Værelser","Etage","Møbleret","Delevenlig","Husdyr tilladt",
    "Elevator","Seniorvenlig","Kun for studerende","Altan/terrasse","Parkering",
    "Opvaskemaskine","Vaskemaskine","Ladestander","Tørretumbler","Energimærke",
    # udlejning
    "Lejeperiode","Ledig fra","Månedlig leje","Aconto","Depositum",
    "Forudbetalt husleje","Indflytningspris","Oprettelsesdato","Sagsnr."
]

def extract_pairs_semantic(soup):
    """
    Try to harvest label/value pairs assuming semantic markup (e.g., <dt>/<dd>)
    around sections titled 'Detaljer om bolig' / 'Detaljer om udlejning'.
    """
    pairs = {}

    def harvest_section(h2_text):
        h2 = soup.find(lambda t: t.name in ("h2","h3") and h2_text in t.get_text(strip=True))
        if not h2:
            return
        section = h2.find_next()
        if not section:
            return
        # 1) definition list pattern
        for dt in section.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                k = clean_text(dt.get_text())
                v = clean_text(dd.get_text(" "))
                pairs[k] = v
        # 2) generic fallback inside section: try two-column rows
        for row in section.find_all(True, recursive=True):
            kids = [k for k in row.children if getattr(k, "get_text", None)]
            if len(kids) == 2:
                k = clean_text(kids[0].get_text())
                v = clean_text(kids[1].get_text(" "))
                if k and v and k in LABELS_ORDER and k not in pairs:
                    pairs[k] = v

    harvest_section("Detaljer om bolig")
    harvest_section("Detaljer om udlejning")
    return pairs

def extract_pairs_by_lines(soup):
    """
    Fallback: scan visible text. When we hit a known label, look ahead up to a few
    lines for a candidate that is NOT another label/header AND (if a validator exists)
    passes that validator. This avoids picking 'Ubegrænset' as Energimærke.
    """
    text = soup.get_text("\n")
    lines = [clean_text(x) for x in text.split("\n")]
    lines = [x for x in lines if x]

    pairs = {}
    wanted = set(LABELS_ORDER)
    header_re = re.compile(r"^Detaljer om (bolig|udlejning)$", re.I)

    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if line in wanted and line not in pairs:
            validator = VALUE_VALIDATORS.get(line)
            # look ahead a limited window so we don't drift too far
            j = i + 1
            steps = 0
            MAX_LOOKAHEAD = 6
            while j < n and steps < MAX_LOOKAHEAD:
                candidate = lines[j]
                # skip if it's another label or a section header
                if candidate in wanted or header_re.match(candidate):
                    break  # hitting another label/header: give up for this label
                # accept first non-empty candidate that passes the validator (if any)
                if candidate:
                    if (validator is None) or validator(candidate):
                        pairs[line] = candidate
                        i = j
                        break
                j += 1
                steps += 1
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
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else None
        elif k == "Værelser":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else None
        elif k == "Etage":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else v
        elif k == "Sagsnr.":
            out[k] = re.sub(r"[^\d]", "", v) or v
        elif k == "Energimærke":
            if v:
                cand = v.strip().upper().replace(" ", "")
                m = re.match(r"^[A-H](\d{4})?$", cand)
                if m:
                    out[k] = m.group(0)
                else:
                    print(f"Unexpected Energimærke value: {v}")
                    out[k] = None
            else:
                out[k] = None
        else:
            out[k] = v
    return out



def scrape_listing(url: str) -> dict:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    status = is_active_listing(r.text, r.status_code)

    soup = BeautifulSoup(r.text, "lxml")
    pairs = extract_pairs_semantic(soup) or extract_pairs_by_lines(soup)
    ordered = {k: pairs.get(k) for k in LABELS_ORDER if k in pairs}
    data = normalize(ordered)  # keep as-is (no url kwarg)

    # --- Energimærke recovery if normalize rejected a bad value/header ---
    if data.get("Energimærke") is None:
        full_text = clean_text(soup.get_text(" "))
        m = re.search(r"\bEnergimærke\b[:\s]*([A-H](?:\d{4})?)\b", full_text, flags=re.I)
        if m:
            data["Energimærke"] = m.group(1).upper()

    # NEW: ensure the key exists, even if we didn't find a value
    if "Energimærke" not in data:
        data["Energimærke"] = None

    # standard extras (as you already have)
    data["url"] = url
    data["listing_id"] = get_listing_id(url)
    data["status"] = status
    data["scraped_at"] = now_iso()

    # robust address extraction (use your new extract_address if you added it)
    street, postcode, city = extract_address(soup)
    data["street"] = street
    data["postcode"] = postcode
    data["city"] = city

    return data



if __name__ == "__main__":
    data = scrape_listing(URL)
    for k, v in data.items():
        print(f"{k}: {v}")
