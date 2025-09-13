# -*- coding: utf-8 -*-
"""
Created on Thu Aug 21 19:44:48 2025

@author: KALSE
"""

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

URL = "https://www.boligportal.dk/lejligheder/horsens/130m2-4-vaer-id-4962343"
HEADERS = {"User-Agent": "research-bot/1.0 (+contact@example.com)"}

# --- helpers ---------------------------------------------------------------

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
    Fallback: scan visible text lines; when we see a known label, the next
    non-empty line becomes its value.
    """
    text = soup.get_text("\n")
    lines = [clean_text(x) for x in text.split("\n")]
    lines = [x for x in lines if x]  # drop empties

    pairs = {}
    wanted = set(LABELS_ORDER)
    i = 0
    while i < len(lines):
        line = lines[i]
        if line in wanted:
            # find the next non-empty line as the value
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            if j < len(lines):
                pairs[line] = lines[j]
                i = j
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
            # "130 m²" -> 130
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else None
        elif k == "Værelser":
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else None
        elif k == "Etage":
            # "3." -> 3
            out[k] = int(re.sub(r"[^\d]", "", v)) if re.search(r"\d", v) else v
        elif k == "Sagsnr.":
            out[k] = re.sub(r"[^\d]", "", v) or v
        else:
            out[k] = v
    return out

def scrape_listing(url=URL):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    status = is_active_listing(r.text, r.status_code)  # <<< NEW LINE
    soup = BeautifulSoup(r.text, "lxml")

    pairs = extract_pairs_semantic(soup)
    if not pairs:
        pairs = extract_pairs_by_lines(soup)

    # Keep only requested labels, preserve order
    ordered = {k: pairs.get(k) for k in LABELS_ORDER if k in pairs}
    data = normalize(ordered)
    data["status"] = status      # <<< ADD FIELD TO OUTPUT
    return data

if __name__ == "__main__":
    data = scrape_listing(URL)
    for k, v in data.items():
        print(f"{k}: {v}")
