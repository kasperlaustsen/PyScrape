# -*- coding: utf-8 -*-
"""
Created on Wed Sep  3 13:17:51 2025

@author: KALSE
"""

# boligportal_collect_urls.py
from __future__ import annotations
import time
import re
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException



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

# ---------- cookie banner (robust) ----------
def _hide_cookie_overlays(driver):
    """Last resort: remove cookie overlays so elements become interactable."""
    js = """
    (function(){
      const sel = [
        '#coiOverlay', '#coiBanner', '#CookieInformationDialog',
        '[id*="cookie"]','[class*="cookie"]','[id*="coi"]','[class*="coi"]'
      ].join(',');
      document.querySelectorAll(sel).forEach(el => { 
        el.style.display='none'; 
        if (el.remove) el.remove(); 
      });
      const html = document.querySelector('html');
      if (html && (html.style.overflow==='hidden' || getComputedStyle(html).overflow==='hidden')) {
          html.style.overflow='auto';
      }
    })();
    """
    driver.execute_script(js)

def _click_by_text_anywhere(driver, texts):
    """Click a button/link whose visible text contains any of the given strings (incl. iframes)."""
    js = """
    const texts = arguments[0].map(t=>t.toLowerCase());
    function tryClick(root){
      const nodes = root.querySelectorAll('button, [role=button], a');
      for (const el of nodes) {
        const t = (el.innerText || el.textContent || '').trim().toLowerCase();
        if (!t) continue;
        for (const want of texts) {
          if (t.includes(want)) { el.click(); return true; }
        }
      }
      return false;
    }
    if (tryClick(document)) return true;
    const iframes = Array.from(document.querySelectorAll('iframe'));
    for (const fr of iframes) {
      try {
        const doc = fr.contentDocument || fr.contentWindow?.document;
        if (doc && tryClick(doc)) return true;
      } catch(e){}
    }
    return false;
    """
    try:
        return driver.execute_script(js, texts)
    except Exception:
        return False

def _accept_cookies_if_present(driver, total_timeout: int = 12) -> bool:
    """
    Cookie Information / Cookiebot hardened accept:
    1) direct known IDs/selectors
    2) click by visible text (incl. iframes)
    3) hide overlays if still blocking
    Returns True if we likely accepted/cleared it.
    """
    t0 = time.time()
    # Direct selectors (Cookie Information / Cookiebot common IDs)
    direct_css = [
        "#coiOverlayAccept",                      # Cookie Information
        "#coiAcceptButton",                       # Cookie Information
        "#CybotCookiebotDialogBodyLevelButtonAccept",            # Cookiebot
        "#CybotCookiebotDialogBodyButtonAccept",
        "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll"
    ]
    while time.time() - t0 < total_timeout:
        # a) direct CSS clicks
        for sel in direct_css:
            try:
                btn = driver.find_element(By.CSS_SELECTOR, sel)
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                btn.click()
                time.sleep(0.4)
                return True
            except Exception:
                pass

        # b) visible-text click (Danish/English variants)
        clicked = _click_by_text_anywhere(driver, [
            "tillad alle", "acceptér alle", "accepter", "godkend",
            "allow all", "accept all", "agree", "i accept"
        ])
        if clicked:
            time.sleep(0.4)
            return True

        time.sleep(0.3)

    # 2) last resort: hide overlays so page becomes interactable
    _hide_cookie_overlays(driver)
    time.sleep(0.3)
    return False

def _visible(e):
    try:
        return e.is_displayed() and e.is_enabled()
    except Exception:
        return False

def _find_search_input(driver, timeout=15):
    """Return the first visible, enabled search input."""
    candidates = [
        "input[placeholder*='Hvor vil du gerne bo']",
        "input[aria-label*='Hvor vil du gerne bo']",
        "input[type='search']",
        "input[name*='location']",
        "[data-testid*='search'] input",
        "[role='search'] input",
    ]
    end = time.time() + timeout
    while time.time() < end:
        for sel in candidates:
            try:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                elems = [e for e in elems if _visible(e)]
                if elems:
                    return elems[0]
            except Exception:
                pass
        time.sleep(0.25)
    raise TimeoutError("Could not find a visible search input")

def _wait_for_navigation_or_results(driver, before_url, timeout=8):
    """Wait until URL changes OR at least one listing link appears."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.current_url != before_url or d.find_elements(By.CSS_SELECTOR, "a[href*='/id-']")
        )
        return True
    except TimeoutException:
        return False

def _type_city_and_submit(driver, city: str):
    """
    Focus the search box, clear, type city, and submit via autosuggest.
    Order: Enter -> ArrowDown+Enter -> click first suggestion -> Søg button -> form submit -> body Enter.
    """
    box = _find_search_input(driver)
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
    time.sleep(0.2)

    # focus & robust clear
    try: box.click()
    except Exception: pass
    try: driver.execute_script("arguments[0].focus();", box)
    except Exception: pass
    time.sleep(0.1)
    try:
        box.send_keys(Keys.CONTROL, "a")
        box.send_keys(Keys.BACKSPACE)
    except Exception:
        pass

    # type city
    before_url = driver.current_url
    box.send_keys(city)
    time.sleep(0.7)  # let autosuggest populate

    # Try 1: plain Enter
    try:
        box.send_keys(Keys.RETURN)
        if _wait_for_navigation_or_results(driver, before_url, timeout=3):
            return
    except Exception:
        pass

    # Try 2: keyboard select first suggestion (single ArrowDown + Enter)
    try:
        box.send_keys(Keys.ARROW_DOWN)
        time.sleep(0.2)
        box.send_keys(Keys.RETURN)
        if _wait_for_navigation_or_results(driver, before_url, timeout=4):
            return
    except Exception:
        pass


    # Try 3: click first visible suggestion in common containers
    try:
        suggest_selectors = [
            "[role='listbox'] [role='option']",
            "ul[role='listbox'] > *",
            "div[role='listbox'] > *",
            ".MuiAutocomplete-popper [role='option']",
            ".autocomplete [role='option']",
            "[data-testid*='suggestion'] li, [data-testid*='suggestion'] div",
        ]
        items = []
        t0 = time.time()
        while time.time() - t0 < 3 and not items:
            for sel in suggest_selectors:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
                elems = [e for e in elems if _visible(e)]
                if elems:
                    items = elems
                    break
            if not items:
                time.sleep(0.2)

        if items:
            # choose the first item containing the city (case-insensitive), else the first item
            pick = None
            for it in items:
                txt = (it.text or "").strip().lower()
                if city.lower() in txt:
                    pick = it
                    break
            if not pick: pick = items[0]
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", pick)
            time.sleep(0.1)
            pick.click()
            if _wait_for_navigation_or_results(driver, before_url, timeout=4):
                return
    except Exception:
        pass

    # Try 4: click a 'Søg' / submit button
    try:
        btn = None
        try:
            parent = box.find_element(By.XPATH, "./ancestor::*[1]")
            btn = parent.find_element(By.XPATH, ".//button[contains(., 'Søg') or @type='submit']")
        except Exception:
            pass
        if not btn:
            btn = driver.find_element(By.XPATH, "//button[contains(., 'Søg') or @type='submit']")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        time.sleep(0.1)
        btn.click()
        if _wait_for_navigation_or_results(driver, before_url, timeout=4):
            return
    except Exception:
        pass

    # Try 5: submit the enclosing form
    try:
        driver.execute_script("if(arguments[0].form){ arguments[0].form.submit(); }", box)
        if _wait_for_navigation_or_results(driver, before_url, timeout=3):
            return
    except Exception:
        pass

    # Try 6: send Enter to the page body
    try:
        body = driver.find_element(By.TAG_NAME, "body")
        ActionChains(driver).move_to_element(body).click().send_keys(Keys.RETURN).perform()
        _wait_for_navigation_or_results(driver, before_url, timeout=3)
    except Exception:
        pass


ID_URL_RE = re.compile(r"""href=["']([^"']*id-\d+[^"']*)["']""", re.IGNORECASE)

def _collect_links_anywhere(driver) -> list[str]:
    """
    Regex-scan the HTML for any href that contains 'id-<digits>'.
    This is robust to complex card markup or delayed anchor creation.
    """
    html = driver.page_source or ""
    hits = ID_URL_RE.findall(html)
    out = []
    seen = set()
    for h in hits:
        try:
            full = h if h.startswith("http") else urljoin(driver.current_url, h)
            if full not in seen:
                seen.add(full); out.append(full)
        except Exception:
            continue
    return out


def _scroll_to_load(driver, rounds=6, pause=0.6):
    """
    Scrolls down in steps to trigger lazy loading.
    """
    for _ in range(rounds):
        driver.execute_script("window.scrollBy(0, Math.max(600, window.innerHeight*0.8));")
        time.sleep(pause)

def _scroll_a_bit(driver):
    try:
        driver.execute_script("window.scrollBy(0, Math.max(400, window.innerHeight*0.6));")
    except Exception:
        pass

def _wait_results_ready(driver, min_links=1, timeout=25):
    """
    Wait until we can see at least `min_links` ad links,
    using the regex-based collector. Scrolls gently while waiting.
    """
    t0 = time.time()
    last_count = 0
    while time.time() - t0 < timeout:
        links = _collect_links_anywhere(driver)
        count = len(links)
        if count >= min_links:
            return True
        # small settle + gentle scroll to trigger lazy renders
        time.sleep(0.6)
        _scroll_a_bit(driver)
        # if nothing new shows up for a few cycles, keep looping until timeout
        last_count = count
    raise TimeoutException("No ad links became visible in time.")


# ---------- helpers ----------
def _collect_listing_links_on_page(driver: webdriver.Chrome) -> list[str]:
    """
    Main collector: regex on page_source + (optional) visible anchors backup.
    """
    urls = set(_collect_links_anywhere(driver))

    # backup: any visible anchors matching /id-
    try:
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='id-']")
        for a in anchors:
            href = a.get_attribute("href")
            if href:
                full = href if href.startswith("http") else urljoin(driver.current_url, href)
                urls.add(full)
    except Exception:
        pass

    return list(urls)


def _try_click_load_more(driver: webdriver.Chrome) -> bool:
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
            time.sleep(0.25)
            before = len(_collect_links_anywhere(driver))
            btn.click()
            time.sleep(0.8)
            _scroll_to_load(driver, rounds=2, pause=0.4)  # help the new batch render
            after = len(_collect_links_anywhere(driver))
            if after > before:
                return True
        except Exception:
            continue
    return False


def _harvest_current_page(driver, seen: set, results: list, city: str, page_no: int, verbose: bool):
    """
    On the current results page:
      - repeatedly collect links
      - scroll and try 'load more'
      - stop after 2 stagnant cycles (no growth)
    """
    stagnant = 0
    while True:
        links = _collect_listing_links_on_page(driver)
        links = [u for u in links if "id-" in u.lower()]

        new = 0
        for L in links:
            if L not in seen:
                seen.add(L)
                results.append(L)
                new += 1

        if verbose:
            print(f"[{city}] page {page_no}: +{new} new this cycle, total={len(results)}")

        grew = new > 0
        clicked = _try_click_load_more(driver)
        _scroll_to_load(driver, rounds=1, pause=0.4)

        if not grew and not clicked:
            stagnant += 1
        else:
            stagnant = 0

        if stagnant >= 2:
            break


def _go_next_page(driver: webdriver.Chrome) -> bool:
    """
    Navigate to the next results page. Returns True if we moved.
    Strategies:
      1) Click explicit 'next' controls (anchors or buttons).
      2) Use numbered pagination: click the sibling after the active page.
      3) Fallback: derive next URL by incrementing ?page= or /page/<n>.
    """
    cur = driver.current_url

    # 1) Obvious "next" controls
    next_locators = [
        # anchors
        (By.CSS_SELECTOR, "a[rel='next']"),
        (By.CSS_SELECTOR, "a[aria-label*='Næste']"),
        (By.CSS_SELECTOR, "a[aria-label*='Next']"),
        (By.XPATH, "//a[.//span[contains(., 'Næste')] or contains(normalize-space(.), 'Næste')]"),
        (By.XPATH, "//a[contains(., 'Næste')]"),
        (By.CSS_SELECTOR, "a.pagination-next"),
        # buttons (some UIs use buttons)
        (By.XPATH, "//button[.//span[contains(., 'Næste')] or contains(normalize-space(.), 'Næste')]"),
        (By.XPATH, "//button[contains(., 'Næste')]"),
        (By.CSS_SELECTOR, "button[aria-label*='Næste']"),
        (By.CSS_SELECTOR, "button[aria-label*='Next']"),
    ]
    for by, sel in next_locators:
        try:
            el = driver.find_element(by, sel)
            if not el.is_displayed():
                continue
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
            time.sleep(0.15)
            before = driver.current_url
            el.click()
            WebDriverWait(driver, 15).until(EC.url_changes(before))
            time.sleep(0.3)
            return True
        except Exception:
            pass

    # 2) Numbered pagination: click the next sibling after active
    try:
        # common patterns for active page item
        active_candidates = [
            # <li class="active"><a>2</a></li> or aria-current
            "//li[contains(@class,'active') or @aria-current='page']",
            # <a aria-current="page">2</a>
            "//a[@aria-current='page']/parent::*",
            # <button aria-current="page">2</button>
            "//button[@aria-current='page']",
        ]
        active = None
        for xp in active_candidates:
            try:
                elt = driver.find_element(By.XPATH, xp)
                if elt.is_displayed():
                    active = elt
                    break
            except Exception:
                continue

        if active is not None:
            # find a clickable next sibling anchor/button
            next_click = None
            for xp in [
                ".//following-sibling::*[1]//a[@href]",
                ".//following-sibling::*[1]//button",
                ".//following-sibling::li[1]//a[@href]",
                ".//following-sibling::*[1]//*[self::a or self::button][@href or normalize-space(.)!='']"
            ]:
                try:
                    cand = active.find_element(By.XPATH, xp)
                    if cand.is_displayed():
                        next_click = cand
                        break
                except Exception:
                    continue

            if next_click is not None:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_click)
                time.sleep(0.15)
                before = driver.current_url
                try:
                    next_click.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", next_click)
                WebDriverWait(driver, 15).until(EC.url_changes(before))
                time.sleep(0.3)
                return True
    except Exception:
        pass

    # 3) URL fallback: increment ?page= or /page/<n>
    try:
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        u = urlparse(cur)
        q = parse_qs(u.query)
        if "page" in q:
            try:
                cur_page = int(q["page"][0])
                q["page"] = [str(cur_page + 1)]
                new_query = urlencode({k: v[0] if isinstance(v, list) and len(v)==1 else v for k, v in q.items()}, doseq=True)
                next_url = urlunparse((u.scheme, u.netloc, u.path, u.params, new_query, u.fragment))
                driver.get(next_url)
                WebDriverWait(driver, 15).until(lambda d: d.current_url != cur)
                time.sleep(0.3)
                return True
            except Exception:
                pass

        # pattern: /page/2 or /side/2
        m = re.search(r"/(page|side)/(\d+)", u.path, re.IGNORECASE)
        if m:
            n = int(m.group(2)) + 1
            next_path = re.sub(r"/(page|side)/\d+", f"/{m.group(1)}/{n}", u.path)
            next_url = urlunparse((u.scheme, u.netloc, next_path, u.params, u.query, u.fragment))
            driver.get(next_url)
            WebDriverWait(driver, 15).until(lambda d: d.current_url != cur)
            time.sleep(0.3)
            return True

        # last resort: look for any numbered page link greater than current page
        try:
            page_links = driver.find_elements(By.XPATH, "//a[@href][normalize-space(text())>= '2' and normalize-space(text())<= '999']")
            # prefer the smallest unseen page number
            cand = None
            nums = []
            for a in page_links:
                t = (a.text or "").strip()
                if t.isdigit():
                    nums.append((int(t), a))
            if nums:
                nums.sort(key=lambda x: x[0])
                cand = nums[0][1]
            if cand and cand.is_displayed():
                before = driver.current_url
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cand)
                time.sleep(0.15)
                cand.click()
                WebDriverWait(driver, 15).until(EC.url_changes(before))
                time.sleep(0.3)
                return True
        except Exception:
            pass

    except Exception:
        pass

    return False

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
        ok = _accept_cookies_if_present(driver, total_timeout=12)
        # Optional debug:
        # print(f"[cookies] dismissed={ok}")
        time.sleep(0.5)  # let modal fully disappear

        # Type city & submit (robust)
        _type_city_and_submit(driver, city)

        print("[debug] current URL after search:", driver.current_url)
        print("[debug] page length:", len(driver.page_source))
        print("[debug] first 500 chars:\n", driver.page_source[:500])

        # Wait for first batch of results
        _wait_results_ready(driver, min_links=1, timeout=25)

        # --- Page 1: harvest everything (scroll + load more) ---
        _harvest_current_page(driver, seen, results, city, page_no=1, verbose=verbose)

        # --- Next pages ---
        page_no = 2
        while page_no <= max_pages:
            if not _go_next_page(driver):
                break
            _wait_results_ready(driver, min_links=1, timeout=20)
            _harvest_current_page(driver, seen, results, city, page_no=page_no, verbose=verbose)
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
