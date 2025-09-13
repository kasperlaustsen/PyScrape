# -*- coding: utf-8 -*-
"""
Created on Wed Sep  3 13:18:27 2025

@author: KALSE
"""

import re
from urllib.parse import urlparse, urlunparse
from scrape_boligportal2 import scrape_listing
import pandas as pd

from boligportal_collect_urls2 import get_city_listing_urls
urls = get_city_listing_urls("Horsens", headless=False, max_pages=100)
len(urls), urls[:5]



ID_RE = re.compile(r"id-(\d+)", re.IGNORECASE)

def clean_and_check(urls):
    # 1) keep only boligportal links (www or bare domain)
    keep = []
    for u in urls:
        try:
            pu = urlparse(u)
            host = (pu.netloc or "").lower()
            if host.endswith("boligportal.dk"):
                # strip query/fragment for stability
                pu = pu._replace(query="", fragment="")
                keep.append(urlunparse(pu))
        except Exception:
            pass

    # 2) index by listing id (id-<digits>)
    id_to_urls = {}
    no_id = []
    for u in keep:
        m = ID_RE.search(u)
        if not m:
            no_id.append(u)
            continue
        lid = m.group(1)
        id_to_urls.setdefault(lid, set()).add(u)

    # 3) report duplicates (same id across different paths)
    duplicates = {lid: sorted(us) for lid, us in id_to_urls.items() if len(us) > 1}

    # 4) choose one canonical URL per id (shortest wins; then lexicographically)
    canonical = {}
    for lid, us in id_to_urls.items():
        us_sorted = sorted(us, key=lambda s: (len(s), s))
        canonical[lid] = us_sorted[0]

    cleaned_urls = sorted(canonical.values())

    # --- reporting ---
    print(f"Input URLs: {len(urls)}")
    print(f"Kept boligportal.dk: {len(keep)}")
    print(f"Unique listing IDs: {len(canonical)}")
    print(f"IDs with duplicates across paths: {len(duplicates)}")
    if no_id:
        print(f"URLs with no id-<digits> pattern: {len(no_id)} (showing up to 5)")
        for u in no_id[:5]:
            print("  ", u)
    if duplicates:
        print("\nExamples of ID duplicates (up to 10):")
        for i, (lid, us) in enumerate(duplicates.items()):
            if i >= 10: break
            print(f"  id-{lid}:")
            for u in us:
                print("    ", u)

    return cleaned_urls, duplicates

# run it
cleaned_urls, duplicate_map = clean_and_check(urls)

# %%



# Step 2: scrape each listing
results = []
for i, url in enumerate(cleaned_urls, 1):
    try:
        data = scrape_listing(url)
        results.append(data)
        print(f"[{i}/{len(urls)}] scraped {url}")
    except Exception as e:
        print(f"[{i}/{len(urls)}] ERROR scraping {url}: {e}")

# Step 3: convert to DataFrame
df = pd.DataFrame(results)

