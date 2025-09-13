# -*- coding: utf-8 -*-
"""
Created on Wed Sep  3 13:17:51 2025

@author: KALSE
"""

# boligportal_collect_urls.py
from __future__ import annotations
import time
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

BASE = "https://www.boligportal.dk"

# ---------- driver setup ----------
def _setup_driver(headless: bool = False) -> webdriver.Chrome:
    opts = Options()
    if headless:
        # for latest Chrome: "--headless=new" also works
        opts.add_argument("--headless")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--lang=da-DK")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts
    )
    driver.set_page_load_timeout(45)
    return driver

# ---------- cookie banner ----------

def _accept_cookies_if_present(driver, timeout: int = 12) -> bool:
    """
    Handle BoligPortal's Cookiebot banner.
    Returns True if clicked, False if not found.
    """
    try:
        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[contains(., 'TILLAD ALLE') or contains(., 'Acceptér alle')]"
            ))
        )
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.2)
        btn.click()
        time.sleep(0.5)
        return True
    except Exception:
        # fallback: try common Cookiebot IDs
        for sel in [
            "#CybotCookiebotDialogBodyLevelButtonAccept",
            "#CybotCookiebotDialogBodyButtonAccept",
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
        ]:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.5)
                return True
            except Exception:
                continue
    return False



# ---------- helpers ----------
def _wait_results_ready(driver: webdriver.Chrome):
    # At least one listing link present
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/id-']")))
    time.sleep(0.4)  # small settle

def _collect_listing_links_on_page(driver: webdriver.Chrome) -> list[str]:
    anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/id-']")
    urls = []
    for a in anchors:
        try:
            href = a.get_attribute("href")
            if href:
                urls.append(href if href.startswith("http") else urljoin(BASE + "/", href))
        except Exception:
            pass
    return urls

def _try_click_load_more(driver: webdriver.Chrome) -> bool:
    # Some result pages may use a "load more" button instead of pagination
    candidates = [
        "//button[contains(., 'Se mere')]",
        "//button[contains(., 'Se flere')]",
        "//button[contains(., 'Vis flere')]",
        "//button[contains(., 'Load more')]",
    ]
    for xp in candidates:
        try:
            btn = driver.find_element(By.XPATH, xp)
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.3)
            before = len(_collect_listing_links_on_page(driver))
            btn.click()
            # Wait for additional items to render
            time.sleep(1.2)
            after = len(_collect_listing_links_on_page(driver))
            if after > before:
                return True
        except Exception:
            pass
    return False

def _go_next_page(driver: webdriver.Chrome) -> bool:
    """
    Try to navigate to the next page. Return True if moved, False otherwise.
    """
    selectors = [
        ("css", "a[rel='next']"),
        ("css", "a[aria-label='Næste']"),
        ("css", "a[aria-label='Next']"),
        ("xpath", "//a[contains(., 'Næste')]"),
        ("css", "a.pagination-next"),
    ]
    for kind, sel in selectors:
        try:
            elem = driver.find_element(By.CSS_SELECTOR, sel) if kind == "css" else driver.find_element(By.XPATH, sel)
            href = elem.get_attribute("href")
            if not href:
                continue
            before = driver.current_url
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", elem)
            time.sleep(0.2)
            elem.click()
            WebDriverWait(driver, 20).until(EC.url_changes(before))
            time.sleep(0.4)
            return True
        except Exception:
            continue
    # Fallback: try load-more style
    return _try_click_load_more(driver)

# ---------- main entry ----------
def get_city_listing_urls(city: str, headless: bool = False, max_pages: int = 100, verbose: bool = True) -> list[str]:
    """
    Open boligportal.dk, type <city> in 'Hvor vil du gerne bo?', and collect
    all listing URLs across all available pages (or until max_pages).
    """
    driver = _setup_driver(headless=headless)
    seen, results = set(), []
    try:
        # Home + cookies
        driver.get(BASE + "/")
        _accept_cookies_if_present(driver)
        time.sleep(1)  # let modal fully disappear

        # Type city and submit (search box uses the prompt text)
        box = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Hvor vil du gerne bo']"))
        )
        box.clear()
        box.send_keys(city)
        box.send_keys(Keys.RETURN)

        # Wait for first batch of results
        _wait_results_ready(driver)

        # Page loop
        page_no = 1
        while page_no <= max_pages:
            links = _collect_listing_links_on_page(driver)
            new = 0
            for L in links:
                if "/id-" in L and L not in seen:
                    seen.add(L)
                    results.append(L)
                    new += 1
            if verbose:
                print(f"[{city}] page {page_no}: +{new} new, total={len(results)}")

            if not _go_next_page(driver):
                break
            page_no += 1

        return results

    finally:
        driver.quit()

# --- quick manual run (works in Spyder: press F5) ---
if __name__ == "__main__":
    city = "Horsens"            # <-- change to your city
    urls = get_city_listing_urls(city, headless=False, max_pages=100, verbose=True)
    print(f"\nCollected {len(urls)} URLs for {city}")
    for u in urls[:10]:
        print("  ", u)
