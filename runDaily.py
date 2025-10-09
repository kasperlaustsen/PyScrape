# -*- coding: utf-8 -*-
"""
Daily scraper for boligportal.dk
Collects all listing URLs for a city, scrapes details, and saves both a current
CSV and a daily snapshot archive.
"""

import os
import re
from urllib.parse import urlparse, urlunparse
import pandas as pd
from datetime import date

from scrape_boligportal2 import scrape_listing
from boligportal_collect_urls2 import get_city_listing_urls

# --- settings ---
CITY = "Horsens"
MAX_PAGES = 100
HEADLESS = True   # run Chrome headless for daily job

SNAPSHOT_DIR = "history"   # archive folder
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

# --- regex for IDs ---
ID_RE = re.compile(r"id-(\d+)", re.IGNORECASE)


def clean_and_check(urls):
    """Filter boligportal.dk links, deduplicate by listing ID, pick canonical URL."""
    keep = []
    for u in urls:
        try:
            pu = urlparse(u)
            host = (pu.netloc or "").lower()
            if host.endswith("boligportal.dk"):
                pu = pu._replace(query="", fragment="")  # strip query/fragment
                keep.append(urlunparse(pu))
        except Exception:
            pass

    id_to_urls = {}
    no_id = []
    for u in keep:
        m = ID_RE.search(u)
        if not m:
            no_id.append(u)
            continue
        lid = m.group(1)
        id_to_urls.setdefault(lid, set()).add(u)

    duplicates = {lid: sorted(us) for lid, us in id_to_urls.items() if len(us) > 1}

    canonical = {}
    for lid, us in id_to_urls.items():
        us_sorted = sorted(us, key=lambda s: (len(s), s))
        canonical[lid] = us_sorted[0]

    cleaned_urls = sorted(canonical.values())

    print(f"Input URLs: {len(urls)}")
    print(f"Kept boligportal.dk: {len(keep)}")
    print(f"Unique listing IDs: {len(canonical)}")
    print(f"IDs with duplicates across paths: {len(duplicates)}")
    if no_id:
        print(f"URLs with no id-<digits>: {len(no_id)} (showing up to 3)")
        for u in no_id[:3]:
            print("   ", u)

    return cleaned_urls


def main():
    # Step 1: collect URLs
    urls = get_city_listing_urls(CITY, headless=HEADLESS, max_pages=MAX_PAGES, verbose=False)
    cleaned_urls = clean_and_check(urls)

    # Step 2: scrape each listing
    results = []
    for i, url in enumerate(cleaned_urls, 1):
        try:
            data = scrape_listing(url)
            results.append(data)
            print(f"[{i}/{len(cleaned_urls)}] scraped {url}")
        except Exception as e:
            print(f"[{i}/{len(cleaned_urls)}] ERROR scraping {url}: {e}")

    df = pd.DataFrame(results)

    # Step 3: save current snapshot
    current_file = f"{CITY}_boligportal.csv"
    df.to_csv(current_file, index=False, encoding="utf-8-sig")
    print(f"\nSaved {len(df)} listings to {current_file}")

    # Step 4: save dated archive snapshot
    today = date.today().isoformat()
    archive_file = os.path.join(SNAPSHOT_DIR, f"{CITY}_boligportal_{today}.csv")
    df.to_csv(archive_file, index=False, encoding="utf-8-sig")
    print(f"Archived snapshot: {archive_file}")


if __name__ == "__main__":
    main()
