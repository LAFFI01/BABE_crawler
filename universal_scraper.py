#!/usr/bin/env python3
"""
Universal Web Scraper - Robust, UI-Based
Supports: tables, lists, cards, JSON APIs, pagination, custom selectors,
          JS-rendered pages (Playwright + Stealth), TLS Bypass (curl_cffi)

Install:
  pip install flask pandas beautifulsoup4 requests lxml curl_cffi openpyxl playwright playwright-stealth
  playwright install chromium
Run:
  python universal_scraper.py
Open:
  http://localhost:5000
"""

import re
import json
import time
import logging
from io import StringIO, BytesIO
from urllib.parse import urljoin, urlparse, urlencode, urlunparse, parse_qs

import requests
from curl_cffi import requests as cffi_requests
import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file, Response, stream_with_context

logging.basicConfig(level=logging.INFO, format="%(levelname)s │ %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# FETCHING
# ─────────────────────────────────────────────────────────────────────────────

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def fetch_requests(url: str, proxy: str = "", auth_token: str = "",
                   retries: int = 3, delay: float = 1.0, timeout: int = 15):
    """Standard requests fetch — fast, no JS."""
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    if auth_token:
        s.headers["Authorization"] = f"Bearer {auth_token}"
    for attempt in range(1, retries + 1):
        try:
            r = s.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"requests attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
    return None


def fetch_curl_cffi(url: str, proxy: str = "", auth_token: str = "",
                       retries: int = 3, delay: float = 1.0, timeout: int = 15):
    """curl_cffi — bypasses Cloudflare/Datadome by impersonating Chrome TLS."""
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    proxies = {"http": proxy, "https": proxy} if proxy else None

    for attempt in range(1, retries + 1):
        try:
            r = cffi_requests.get(
                url, 
                impersonate="chrome120", 
                headers=headers, 
                proxies=proxies, 
                timeout=timeout
            )
            r.raise_for_status()
            return r
        except Exception as e:
            log.warning(f"curl_cffi attempt {attempt}/{retries}: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
    return None


def fetch_playwright(url: str, proxy: str = "", timeout: int = 30,
                     wait_selector: str = "", scroll: bool = True) -> str | None:
    """
    Headless Chromium with optional Stealth — fully renders JS, handles SPAs.
    Returns raw HTML string.
    Falls back gracefully if stealth module fails.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.error("Playwright not installed. Run: pip install playwright && playwright install chromium")
        return None

    stealth_available = False
    try:
        from playwright_stealth import stealth_sync
        stealth_available = True
    except ImportError:
        log.warning("playwright_stealth not installed (optional, continuing without stealth)")

    try:
        with sync_playwright() as p:
            launch_opts = {"headless": True, "args": ["--disable-blink-features=AutomationControlled"]}
            if proxy:
                launch_opts["proxy"] = {"server": proxy}

            browser = p.chromium.launch(**launch_opts)
            ctx = browser.new_context(
                user_agent=BROWSER_HEADERS["User-Agent"],
                locale="en-US",
                viewport={"width": 1280, "height": 900},
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": BROWSER_HEADERS["Accept"],
                },
            )
            page = ctx.new_page()
            
            # Apply anti-bot stealth mechanisms if available
            if stealth_available:
                try:
                    stealth_sync(page)
                    log.info("Stealth mode applied")
                except Exception as e:
                    log.warning(f"Stealth mode failed (continuing): {e}")

            # Block images/fonts to speed up loading
            page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,eot}",
                       lambda r: r.abort())

            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            log.info(f"Playwright: page loaded for {url}")

            # Try to close any modal/consent banners gracefully
            for close_sel in ("[aria-label='Close']", "[class*='close'], .btn-close, [class*='dismiss']"):
                try:
                    page.click(close_sel, timeout=1000)
                    log.info("Closed modal/banner")
                    time.sleep(0.3)
                except Exception:
                    pass

            # Try to click "Load More" buttons or pagination if available
            for load_btn_sel in (
                "button:has-text('Load More')",
                "button:has-text('Show more')",
                "a:has-text('Load More')",
                "[class*='load-more']",
                "[class*='load_more']",
                "[class*='show-more']",
            ):
                try:
                    btn = page.query_selector(load_btn_sel)
                    if btn and btn.is_visible():
                        log.info(f"Found load button: {load_btn_sel}, clicking...")
                        page.click(load_btn_sel)
                        time.sleep(1)
                except Exception:
                    pass

            # Wait for network to idle (critical for SPAs)
            try:
                page.wait_for_load_state("networkidle", timeout=timeout * 1000)
                log.info("Network idle reached")
            except PWTimeout:
                log.warning("Network idle timeout, continuing anyway")
            except Exception as e:
                log.warning(f"Wait for load state failed: {e}, continuing")

            # Wait for content to appear
            selectors_to_try = [
                wait_selector,
                "article", "[class*='company']", "[class*='card']",
                "[class*='listing']", "[class*='item']", "main", "[role='main']",
            ]
            found_content = False
            for sel in selectors_to_try:
                if not sel:
                    continue
                try:
                    page.wait_for_selector(sel, timeout=3000)
                    log.info(f"Playwright: found selector '{sel}'")
                    found_content = True
                    break
                except (PWTimeout, Exception):
                    continue

            if not found_content:
                log.info("No recognized selectors found, proceeding with full page")

            # Scroll to trigger lazy-loaded content
            if scroll:
                try:
                    # More aggressive scrolling for infinite-scroll sites
                    for i in range(8):  # Increased from 4
                        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")  # Scroll more per iteration
                        time.sleep(0.3)
                        # Check if more content loaded
                        current_height = page.evaluate("document.body.scrollHeight")
                        log.info(f"  Scroll {i+1}: page height = {current_height}")
                    page.evaluate("window.scrollTo(0, 0)")
                    time.sleep(0.2)
                except Exception as e:
                    log.warning(f"Scroll failed: {e}")

            html = page.content()
            html_len = len(html)
            
            # Detailed diagnostics about what we captured
            soup_diag = BeautifulSoup(html, "lxml")
            articles = soup_diag.find_all("article")
            divs_with_class = soup_diag.find_all("div", class_=True)
            lis = soup_diag.find_all("li")
            
            log.info(f"Playwright: captured {html_len} bytes")
            log.info(f"  → {len(articles)} <article> elements")
            log.info(f"  → {len(divs_with_class)} <div class=...> elements")
            log.info(f"  → {len(lis)} <li> elements")
            
            # Log a sample of div classes to help diagnose structure
            if divs_with_class:
                sample_classes = [d.get("class", []) for d in divs_with_class[:10]]
                log.info(f"  → Sample div classes: {sample_classes}")
            
            # Proper cleanup to prevent memory leaks
            try:
                page.close()
                ctx.close()
            except:
                pass
            try:
                browser.close()
            except:
                pass
            
            if html_len < 500:
                log.warning(f"Playwright returned suspiciously small HTML ({html_len} bytes), may fail extraction")
            
            return html

    except Exception as e:
        log.error(f"Playwright fetch critical failure for {url}: {e}", exc_info=True)
        try:
            browser.close()
        except:
            pass
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_content_type(soup: BeautifulSoup, url: str) -> str:
    pre = soup.find("pre")
    if pre:
        try:
            json.loads(pre.get_text())
            return "json"
        except ValueError:
            pass

    if soup.find("table"):
        return "table"

    for tag in ("article", "li"):
        elems = soup.find_all(tag)
        if len(elems) >= 3:
            texts = [e.get_text(strip=True) for e in elems[:5]]
            if all(len(t) > 20 for t in texts):
                return "cards"

    divs = soup.find_all("div", class_=True)
    class_counts: dict[str, int] = {}
    for d in divs:
        cls = " ".join(d.get("class", []))
        class_counts[cls] = class_counts.get(cls, 0) + 1
    if any(v >= 5 for v in class_counts.values()):
        return "cards"

    return "cards"


def detect_next_page(soup: BeautifulSoup, current_url: str) -> str | None:
    # 1. Try rel="next" link
    nxt = soup.find("a", rel=lambda r: r and "next" in r)
    if nxt and nxt.get("href"):
        return urljoin(current_url, nxt["href"])

    # 2. Try text-based next links
    for a in soup.find_all("a", href=True):
        txt = a.get_text(strip=True).lower()
        if txt in ("next", "next »", "»", "›", ">", "next page", "→"):
            return urljoin(current_url, a["href"])

    # 3. Try incrementing query parameter (page, p, pg, offset, start)
    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query)
    for key in ("page", "p", "pg", "offset", "start"):
        if key in qs:
            try:
                val = int(qs[key][0])
                qs[key] = [str(val + 1)]
                new_qs = urlencode({k: v[0] for k, v in qs.items()})
                next_url = urlunparse(parsed._replace(query=new_qs))
                log.info(f"detect_next_page: incrementing {key} from {val} to {val+1}")
                return next_url
            except ValueError:
                pass

    # 4. Try /page/N URL pattern
    m = re.search(r"(/page/)(\d+)", current_url)
    if m:
        next_num = int(m.group(2)) + 1
        next_url = current_url[: m.start(2)] + str(next_num) + current_url[m.end(2):]
        log.info(f"detect_next_page: incrementing /page/ from {m.group(2)} to {next_num}")
        return next_url

    # 5. If no pagination found, assume first page - try adding ?page=2
    if "?" not in current_url and "/page/" not in current_url:
        next_url = current_url + "?page=2"
        log.info(f"detect_next_page: no pagination found, trying {next_url}")
        return next_url

    return None


# ─────────────────────────────────────────────────────────────────────────────
# EXTRACTORS
# ─────────────────────────────────────────────────────────────────────────────

def extract_table(soup: BeautifulSoup, selector: str = "") -> list[dict]:
    rows = []
    tables = soup.select(selector) if selector else soup.find_all("table")
    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        data_rows = table.find_all("tr")
        if not headers and data_rows:
            headers = [td.get_text(strip=True) for td in data_rows[0].find_all("td")]
            data_rows = data_rows[1:]
        else:
            data_rows = data_rows[1:]
        if not headers:
            continue
        for tr in data_rows:
            cols = tr.find_all(["td", "th"])
            if not cols:
                continue
            row = {}
            for i, td in enumerate(cols):
                key = headers[i] if i < len(headers) else f"col_{i}"
                a = td.find("a")
                if a:
                    row[key] = a.get_text(strip=True)
                    if a.get("href"):
                        row[f"{key}_url"] = a["href"]
                else:
                    row[key] = td.get_text(strip=True)
            if any(v.strip() for v in row.values() if isinstance(v, str)):
                rows.append(row)
    return rows


def extract_cards(soup: BeautifulSoup, selector: str = "") -> list[dict]:
    rows = []
    if selector:
        candidates = soup.select(selector)
    else:
        best, best_score = [], 0
        for tag in ("article", "li", "div"):
            seen_classes: dict[str, list] = {}
            for el in soup.find_all(tag, class_=True):
                cls = " ".join(sorted(el.get("class", [])))
                seen_classes.setdefault(cls, []).append(el)
            for cls, elems in seen_classes.items():
                avg_len = sum(len(e.get_text(strip=True)) for e in elems) / len(elems)
                score = len(elems) * min(avg_len, 200)
                if score > best_score and len(elems) >= 3 and avg_len > 30:
                    best_score, best = score, elems
        candidates = best
        if best:
            log.info(f"extract_cards: found {len(best)} candidates (score={best_score})")
        else:
            log.info("extract_cards: no suitable card candidates found")

    for elem in candidates:
        data = {}
        
        # Get full text for parsing
        full_text = elem.get_text(strip=True)
        
        # Extract profile URL first (more reliable)
        links = elem.find_all("a", href=True)
        for a in links:
            href = a.get("href", "")
            if "/company/" in href:
                data["profile_url"] = href
                # Derive company name from URL slug as fallback
                slug = href.split("/company/")[1].rstrip("#/")
                company_name_from_url = slug.replace("-", " ").title()
                data["_name_fallback"] = company_name_from_url
                break
        
        # Try to extract company name from text (between awards and "Verified")
        # Look for capitalized words after stripping awards prefix
        words = full_text.split()
        name_words = []
        skip_award = True
        for word in words:
            # Skip award-related words
            if any(x in word.lower() for x in ["award", "winner", "2025", "2024", "2023"]):
                skip_award = True
                continue
            # Start collecting name after awards
            if skip_award and word[0].isupper() and len(word) > 2:
                skip_award = False
            # Collect words until we hit "Verified"
            if not skip_award:
                if "verified" in word.lower():
                    break
                name_words.append(word)
        
        if name_words:
            company_nametext = " ".join(name_words).strip()
            # Clean up any remaining junk
            company_nametext = re.sub(r'\d+\s*(Awards?|Winners?)', '', company_nametext, flags=re.IGNORECASE).strip()
            if company_nametext and len(company_nametext) > 2:
                data["name"] = company_nametext[:100]
        
        # Fallback to URL-derived name if text extraction failed
        if not data.get("name") and data.get("_name_fallback"):
            data["name"] = data.pop("_name_fallback")
        
        # Extract location: City, Nepal pattern
        location_match = re.search(r"([A-Z][a-z]+),?\s*Nepal", full_text)
        if location_match:
            data["location"] = location_match.group(1).strip() + ", Nepal"
        
        # Extract description from paragraphs first
        for p in elem.find_all("p"):
            txt = p.get_text(strip=True)
            if (len(txt) > 20 and 
                not any(x in txt.lower() for x in ["award", "winner", "verified"])):
                data["description"] = txt[:300]
                break
        
        # Extract rating
        rating_el = elem.find(class_=re.compile(r"rating|score|star|review", re.I))
        if rating_el:
            nums = re.findall(r"\d+\.?\d*", rating_el.get_text())
            if nums:
                data["rating"] = nums[0]

        # Extract tags/services - more comprehensive
        services = []
        
        # 1. Look for elements with common service/skill class names
        service_selectors = [
            {'class': lambda x: x and any(p in str(x).lower() for p in ['tag', 'badge', 'skill', 'service', 'category']) if x else False},
            {'class': lambda x: x and 'chip' in str(x).lower() if x else False},
        ]
        
        for selector in service_selectors:
            tag_els = elem.find_all(['span', 'div', 'a'], selector)
            for tag_el in tag_els:
                service_text = tag_el.get_text(strip=True)
                if (service_text and 
                    len(service_text) > 1 and 
                    len(service_text) < 50 and
                    not any(x in service_text.lower() for x in ['award', 'winner', 'verified', 'hire', 'hourly', 'rate', '|', '×'])):
                    services.append(service_text)
        
        # 2. Look for comma-separated or pipe-separated text that looks like services
        # Often in a list item or paragraph
        for list_elem in elem.find_all(['ul', 'ol']):
            for li in list_elem.find_all('li'):
                service_text = li.get_text(strip=True)
                if service_text and len(service_text) < 50:
                    services.append(service_text)
        
        # Deduplicate and clean
        services = list(dict.fromkeys(services))  # Remove duplicates while preserving order
        if services:
            data["services"] = services  # Store as list instead of comma-separated

        # Extract price/rate
        price_el = elem.find(string=re.compile(r"\$\d+|\d+\s*/\s*hr|hour", re.I))
        if price_el:
            data["rate"] = price_el.strip()

        # Remove fallback key before returning
        data.pop("_name_fallback", None)

        # Accept if has profile_url
        if data.get("profile_url"):
            rows.append(data)

    log.info(f"extract_cards: extracted {len(rows)} records")
    return rows


def extract_json_api(response) -> list[dict]:
    try:
        data = response.json()
    except Exception:
        return []
    if isinstance(data, list):
        return [d if isinstance(d, dict) else {"value": d} for d in data]
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list) and v:
                return [d if isinstance(d, dict) else {"value": d} for d in v]
        return [data]
    return []


def extract_custom(soup: BeautifulSoup, field_map: dict) -> list[dict]:
    if not field_map:
        return []
    anchor_sel, anchor_count = "", 0
    for sel in field_map.values():
        n = len(soup.select(sel))
        if n > anchor_count:
            anchor_count, anchor_sel = n, sel
    if not anchor_count:
        return []

    pools = {name: soup.select(sel) for name, sel in field_map.items()}
    rows = []
    for _ in soup.select(anchor_sel):
        row = {}
        for name in field_map:
            if pools[name]:
                el = pools[name].pop(0)
                a = el.find("a")
                row[name] = a.get_text(strip=True) if a else el.get_text(strip=True)
                if a and a.get("href") and name.lower() in ("name", "title", "company"):
                    row[f"{name}_url"] = a["href"]
        if any(row.values()):
            rows.append(row)
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# MAIN SCRAPE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def extract_services_from_description(description: str) -> list:
    """Extract potential services from company description using keywords."""
    if not description:
        return []
    
    # Common IT/business services
    common_services = [
        'web development', 'web design', 'app development', 'mobile app', 'software development',
        'seo', 'digital marketing', 'content marketing', 'social media marketing', 'advertising',
        'brand design', 'ui design', 'ux design', 'graphic design', 'logo design',
        'cloud services', 'it consulting', 'managed services', 'devops', 'cybersecurity',
        'ecommerce', 'cms', 'wordpress', 'drupal', 'magento',
        'python', 'nodejs', 'react', 'angular', 'vue', 'django', 'flask',
        'data analytics', 'business intelligence', 'machine learning', 'artificial intelligence', 'ai',
        'blockchain', 'cryptocurrency', 'web3', 'smart contracts',
        'business consulting', 'strategy consulting', 'it strategy', 'digital transformation',
        'automation', 'process automation', 'rpa',
        'email marketing', 'marketing automation', 'crm', 'analytics',
        'video production', 'animation', 'motion graphics', 'photography',
        'copywriting', 'technical writing', 'translation', 'localization',
        'testing', 'qa testing', 'performance testing', 'security testing', 'automation testing',
        'deployment', 'infrastructure', 'hosting', 'aws', 'azure', 'gcp',
    ]
    
    desc_lower = description.lower()
    found_services = []
    
    for service in common_services:
        if service in desc_lower:
            # Avoid partial matches (e.g., "app" matching "application")
            # Only match complete words or phrases
            pattern = r'\b' + re.escape(service) + r'\b'
            if re.search(pattern, desc_lower):
                found_services.append(service.title())
    
    # Deduplicate
    return list(dict.fromkeys(found_services))


def fetch_company_services(profile_url: str, timeout: int = 10) -> list:
    """Fetch services from individual company profile page."""
    if not profile_url:
        return []
    
    try:
        # Make absolute URL if needed
        if profile_url.startswith('/'):
            profile_url = "https://www.techbehemoths.com" + profile_url
        
        html = fetch_playwright(profile_url, timeout=timeout, wait_selector="article")
        if not html:
            return []
        
        soup = BeautifulSoup(html, "lxml")
        services = []
        
        # Strategy 1: Find "Services" heading and collect following short text
        found_services_section = False
        for elem in soup.find_all(['h1', 'h2', 'h3', 'span', 'div']):
            text = elem.get_text(strip=True)
            
            # Look for services section
            if text.lower() == 'services' or 'services' in text.lower():
                found_services_section = True
                # Collect all following short text elements
                for sibling in elem.find_next_siblings():
                    s_text = sibling.get_text(strip=True)
                    
                    # Stop at next section
                    if sibling.name in ['h1', 'h2', 'h3', 'h4']:
                        break
                    
                    # Take short, non-link text as services
                    if s_text and 3 < len(s_text) < 50:
                        if not any(x in s_text.lower() for x in ['discover', 'see all', 'browse', 'match', 'contact', 'inquire']):
                            # Clean up
                            s_text = s_text.split('|')[0].strip()
                            if s_text and len(services) < 15:
                                services.append(s_text)
                    
                    if len(services) > 10:
                        break
                
                if services:
                    break
        
        # Strategy 2: Look for common service text patterns in all text nodes
        if not services:
            text_content = soup.get_text()
            # Look for "Services" keyword and capture what follows
            if 'Services' in text_content:
                idx = text_content.find('Services')
                snippet = text_content[idx:idx+500]
                # Extract service names (typically capitalized, comma or line separated)
                lines = snippet.split('\n')
                for line in lines[1:]:
                    line = line.strip()
                    if line and 3 < len(line) < 50 and line[0].isupper():
                        services.append(line)
                    if len(services) >= 8:
                        break
        
        # Dedupe
        services = list(dict.fromkeys(services))
        return services[:10]
    
    except Exception as e:
        log.debug(f"Failed to fetch services from {profile_url}: {e}")
        return []


def scrape(config: dict):
    url           = config["url"]
    max_pages     = int(config.get("max_pages", 1))
    delay         = float(config.get("delay", 1.0))
    mode          = config.get("mode", "auto")
    selector      = config.get("selector", "")
    field_map     = config.get("field_map", {})
    proxy         = config.get("proxy", "")
    auth_token    = config.get("auth_token", "")
    retries       = int(config.get("retries", 3))
    timeout       = int(config.get("timeout", 30))
    fetch_mode    = config.get("fetch_mode", "auto")
    wait_selector = config.get("wait_selector", "")

    all_rows: list[dict] = []
    page = 1
    current_url = url

    while page <= max_pages and current_url:
        yield {"type": "progress", "page": page, "url": current_url, "count": len(all_rows)}

        html_text = None
        resp_obj  = None
        used_fetch = fetch_mode

        if fetch_mode == "browser":
            html_text = fetch_playwright(current_url, proxy=proxy,
                                         timeout=timeout, wait_selector=wait_selector)
            if not html_text:
                yield {"type": "error", "msg": f"Browser fetch failed: {current_url}"}
                break

        elif fetch_mode == "curl_cffi":
            resp_obj = fetch_curl_cffi(current_url, proxy=proxy,
                                          auth_token=auth_token,
                                          retries=retries, delay=delay, timeout=timeout)
            if not resp_obj:
                yield {"type": "error", "msg": f"TLS Bypass failed: {current_url}"}
                break
            html_text = resp_obj.text

        elif fetch_mode == "requests":
            resp_obj = fetch_requests(current_url, proxy=proxy,
                                      auth_token=auth_token,
                                      retries=retries, delay=delay, timeout=timeout)
            if not resp_obj:
                yield {"type": "error", "msg": f"Requests failed: {current_url}"}
                break
            html_text = resp_obj.text

        else:  # auto escalation
            resp_obj = fetch_requests(current_url, proxy=proxy, auth_token=auth_token,
                                      retries=1, delay=delay, timeout=timeout)
            if resp_obj:
                used_fetch = "requests"
                html_text = resp_obj.text
            else:
                yield {"type": "info", "msg": "requests blocked → trying TLS Bypass (curl_cffi)…"}
                resp_obj = fetch_curl_cffi(current_url, proxy=proxy, auth_token=auth_token,
                                              retries=retries, delay=delay, timeout=timeout)
                if resp_obj:
                    used_fetch = "curl_cffi"
                    html_text = resp_obj.text
                else:
                    yield {"type": "info", "msg": "TLS Bypass blocked → launching headless browser…"}
                    html_text = fetch_playwright(current_url, proxy=proxy, timeout=timeout,
                                                 wait_selector=wait_selector)
                    used_fetch = "browser"
                    if not html_text:
                        yield {"type": "error", "msg": f"All fetch methods failed: {current_url}"}
                        break

        if resp_obj:
            ct = resp_obj.headers.get("Content-Type", "")
            if "json" in ct or mode == "json":
                rows = extract_json_api(resp_obj)
                all_rows.extend(rows)
                yield {"type": "page_done", "page": page, "rows": len(rows),
                       "mode": "json", "fetch": used_fetch}
                break

        soup = BeautifulSoup(html_text, "lxml")
        mode_used = detect_content_type(soup, current_url) if mode == "auto" else mode

        if mode_used == "table":
            rows = extract_table(soup, selector)
        elif mode_used == "cards":
            rows = extract_cards(soup, selector)
        elif mode_used == "custom":
            rows = extract_custom(soup, field_map)
        else:
            rows = extract_table(soup, selector) or extract_cards(soup, selector)

        # Debug: why did extraction fail?
        if not rows:
            articles = soup.find_all("article")
            divs = soup.find_all("div", class_=True)
            lis = soup.find_all("li")
            log.warning(f"Extraction found 0 rows. Page structure: {len(articles)} <article>, {len(divs)} <div class>, {len(lis)} <li>")

        if not rows and mode == "auto":
            alt = extract_table(soup, selector) if mode_used == "cards" else extract_cards(soup, selector)
            if alt:
                rows = alt
                mode_used = "table" if mode_used == "cards" else "cards"

        if (not rows and mode == "auto" and used_fetch == "curl_cffi" and mode != "json"):
            yield {"type": "info", "msg": "0 rows from curl_cffi → launching browser for JS rendering…"}
            html_text_browser = fetch_playwright(current_url, proxy=proxy, timeout=timeout,
                                                 wait_selector=wait_selector)
            if html_text_browser:
                used_fetch = "browser"
                soup = BeautifulSoup(html_text_browser, "lxml")
                rows = extract_cards(soup, selector) or extract_table(soup, selector)
                if not rows:
                    rows = extract_table(soup, selector) or extract_cards(soup, selector)
                if not rows:
                    articles = soup.find_all("article")
                    divs = soup.find_all("div", class_=True)
                    log.warning(f"Browser extraction also found 0 rows. Structure: {len(articles)} <article>, {len(divs)} <div class>")

        all_rows.extend(rows)
        
        # Optional: Extract services from company descriptions
        fetch_services = config.get("fetch_services", False) or config.get("extract_services", False)
        if fetch_services and rows:
            for row in rows:
                if row.get("description") and "services" not in row:
                    services = extract_services_from_description(row["description"])
                    if services:
                        row["services"] = services
        
        yield {"type": "page_done", "page": page, "rows": len(rows),
               "mode": mode_used, "fetch": used_fetch}

        if page >= max_pages:
            break

        next_url = detect_next_page(soup, current_url)
        if not next_url or next_url == current_url:
            yield {"type": "info", "msg": "No further pages detected."}
            break

        current_url = next_url
        page += 1
        time.sleep(delay)

    yield {"type": "done", "total": len(all_rows), "data": all_rows}


# ─────────────────────────────────────────────────────────────────────────────
# FLASK ROUTES
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return FRONTEND_HTML


@app.route("/scrape", methods=["POST"])
def run_scrape():
    config = request.get_json(force=True)

    def generate():
        for event in scrape(config):
            yield f"data: {json.dumps(event)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/export/<fmt>", methods=["POST"])
def export(fmt: str):
    data = request.get_json(force=True).get("data", [])
    
    # Convert services list to string for CSV compatibility
    if fmt == "csv":
        data = [{**row, "services": ", ".join(row["services"]) if isinstance(row.get("services"), list) else row.get("services", "")} for row in data]
    
    df = pd.DataFrame(data)

    if fmt == "csv":
        buf = StringIO()
        df.to_csv(buf, index=False, encoding="utf-8")
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=scraped_data.csv"})

    if fmt == "json":
        return Response(df.to_json(orient="records", force_ascii=False),
                        mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=scraped_data.json"})

    if fmt == "excel":
        # FIXED: Removed tempfile leak, using BytesIO directly into memory
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
        buf.seek(0)
        return send_file(
            buf, 
            as_attachment=True, 
            download_name="scraped_data.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    return jsonify({"error": "Unknown format"}), 400


# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND
# ─────────────────────────────────────────────────────────────────────────────

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Universal Web Scraper</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #0e1117; --surface: #161b27; --surface2: #1e2535;
    --border: #2a3047; --accent: #4f8ef7; --accent2: #6c63ff;
    --success: #22d3a4; --warning: #f5a623; --danger: #f05252;
    --text: #e8ecf4; --muted: #7a8394;
    --font: 'Segoe UI', system-ui, sans-serif;
    --mono: 'Cascadia Code', 'Fira Code', monospace;
  }
  body { background: var(--bg); color: var(--text); font-family: var(--font); font-size: 14px; min-height: 100vh; }

  header {
    padding: 16px 28px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 12px; background: var(--surface);
  }
  .logo { font-size: 20px; font-weight: 700; letter-spacing: -0.5px; }
  .logo span { color: var(--accent); }
  .badge { font-size: 11px; background: var(--accent2); color: #fff; padding: 2px 8px; border-radius: 20px; font-weight: 600; }

  .layout { display: grid; grid-template-columns: 370px 1fr; height: calc(100vh - 57px); }

  .sidebar {
    background: var(--surface); border-right: 1px solid var(--border);
    overflow-y: auto; padding: 18px; display: flex; flex-direction: column; gap: 14px;
  }

  .section-label { font-size: 10px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); margin-bottom: 6px; font-weight: 700; }

  label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 3px; }

  input, select, textarea {
    width: 100%; background: var(--surface2); border: 1px solid var(--border);
    color: var(--text); border-radius: 6px; padding: 7px 9px;
    font-size: 13px; font-family: var(--font); outline: none; transition: border-color 0.15s;
  }
  input:focus, select:focus, textarea:focus { border-color: var(--accent); }
  textarea { resize: vertical; min-height: 60px; font-family: var(--mono); font-size: 12px; }

  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }

  .fetch-pills { display: flex; gap: 6px; flex-wrap: wrap; }
  .fetch-pill {
    padding: 5px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;
    cursor: pointer; border: 1px solid var(--border); background: var(--surface2);
    color: var(--muted); transition: all 0.15s; user-select: none;
  }
  .fetch-pill:hover { border-color: var(--accent); color: var(--accent); }
  .fetch-pill.active            { background: var(--accent);   border-color: var(--accent);   color: #fff; }
  .fetch-pill.pill-browser.active { background: #6c63ff;        border-color: #6c63ff;         color: #fff; }
  .fetch-pill.pill-cloud.active   { background: var(--warning); border-color: var(--warning);  color: #000; }

  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    padding: 8px 14px; border-radius: 7px; font-size: 13px; font-weight: 600;
    cursor: pointer; border: none; transition: all 0.15s; width: 100%;
  }
  .btn-primary { background: var(--accent); color: #fff; font-size: 14px; padding: 11px; }
  .btn-primary:hover { background: #3a7af0; }
  .btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
  .btn-stop { background: var(--surface2); color: var(--danger); border: 1px solid var(--danger); }
  .btn-stop:hover { background: var(--danger); color: #fff; }
  .btn-sm { background: var(--surface2); color: var(--text); border: 1px solid var(--border); width: auto; padding: 6px 12px; }
  .btn-sm:hover { border-color: var(--accent); color: var(--accent); }
  .btn-clear { background: var(--surface2); color: var(--danger); border: 1px solid var(--border); width: auto; padding: 6px 12px; }
  .btn-clear:hover { border-color: var(--danger); }

  .main { display: flex; flex-direction: column; overflow: hidden; }
  .toolbar {
    padding: 10px 18px; border-bottom: 1px solid var(--border);
    display: flex; align-items: center; gap: 8px; background: var(--surface); flex-wrap: wrap;
  }
  .status-bar {
    padding: 5px 18px; background: var(--surface2); border-bottom: 1px solid var(--border);
    font-size: 12px; color: var(--muted); display: flex; gap: 16px; align-items: center; min-height: 30px;
  }
  .status-pill {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 9px; border-radius: 20px; font-size: 11px; font-weight: 700; white-space: nowrap;
  }
  .status-pill.idle    { background: #2a3047;               color: var(--muted);    }
  .status-pill.running { background: rgba(79,142,247,.15);  color: var(--accent);   }
  .status-pill.done    { background: rgba(34,211,164,.15);  color: var(--success);  }
  .status-pill.error   { background: rgba(240,82,82,.15);   color: var(--danger);   }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
  .dot.pulse { animation: pulse 1s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.25} }

  .fetch-tag {
    font-size: 10px; font-weight: 700; padding: 1px 7px; border-radius: 10px; margin-left: 4px;
  }
  .fetch-tag.requests     { background: rgba(34,211,164,.15);  color: var(--success); }
  .fetch-tag.curl_cffi    { background: rgba(245,166,35,.15);  color: var(--warning); }
  .fetch-tag.browser      { background: rgba(108,99,255,.2);   color: #a89cff;        }

  .progress-bar-wrap { height: 3px; background: var(--border); }
  .progress-bar { height: 100%; background: var(--accent); transition: width .3s; }

  .log-panel {
    padding: 8px 18px; border-bottom: 1px solid var(--border);
    font-family: var(--mono); font-size: 11.5px; color: var(--muted);
    height: 88px; overflow-y: auto; background: #090c13;
  }
  .log-entry      { padding: 1px 0; }
  .log-entry.ok   { color: var(--success); }
  .log-entry.warn { color: var(--warning); }
  .log-entry.err  { color: var(--danger);  }
  .log-entry.info { color: #a89cff;        }

  .table-wrap { flex: 1; overflow: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    background: var(--surface); padding: 9px 13px; text-align: left;
    font-weight: 700; font-size: 10px; text-transform: uppercase; letter-spacing: .6px;
    color: var(--muted); border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 1; white-space: nowrap;
  }
  td {
    padding: 8px 13px; border-bottom: 1px solid rgba(42,48,71,.5);
    max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  td a { color: var(--accent); text-decoration: none; }
  td a:hover { text-decoration: underline; }
  tr:hover td { background: rgba(255,255,255,.02); }

  .empty-state {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; height: 100%; gap: 10px; color: var(--muted);
  }
  .empty-icon { font-size: 44px; opacity: .25; }

  .field-map-row {
    display: grid; grid-template-columns: 1fr 1fr 26px; gap: 5px;
    align-items: center; margin-bottom: 5px;
  }
  .icon-btn { background: none; border: none; color: var(--danger); cursor: pointer; font-size: 18px; line-height: 1; padding: 0; }

  .search-box {
    background: var(--surface2); border: 1px solid var(--border); border-radius: 6px;
    padding: 5px 10px; color: var(--text); font-size: 13px; outline: none; width: 190px;
  }
  .search-box:focus { border-color: var(--accent); }

  .tip {
    font-size: 11px; color: var(--muted); background: rgba(79,142,247,.08);
    border: 1px solid rgba(79,142,247,.2); border-radius: 6px; padding: 6px 9px; line-height: 1.6;
  }
  .tip b { color: var(--accent); }
</style>
</head>
<body>

<header>
  <div class="logo">Web<span>Scraper</span></div>
  <div class="badge">Universal</div>
  <span style="margin-left:auto;font-size:12px;color:var(--muted)">requests · curl_cffi · playwright-stealth</span>
</header>

<div class="layout">

<div class="sidebar">

  <div>
    <div class="section-label">Target URL</div>
    <input type="url" id="url" placeholder="https://example.com/listings" />
  </div>

  <div>
    <div class="section-label">Fetch Strategy</div>
    <div class="fetch-pills">
      <div class="fetch-pill active"       data-val="auto"      onclick="setPill(this)">⚡ Auto</div>
      <div class="fetch-pill"              data-val="requests"  onclick="setPill(this)">🌐 Requests</div>
      <div class="fetch-pill pill-cloud"   data-val="curl_cffi" onclick="setPill(this)">🛡 Bypass (TLS)</div>
      <div class="fetch-pill pill-browser" data-val="browser"   onclick="setPill(this)">🖥 Browser</div>
    </div>
    <div class="tip" style="margin-top:8px">
      <b>Auto</b> escalates: Requests → TLS Bypass → Browser.<br>
      Use <b>🖥 Browser</b> for JS/SPA sites.
    </div>
  </div>

  <div id="wait-sel-row" style="display:none">
    <div class="section-label">Wait for Selector <span style="font-size:10px">(browser mode)</span></div>
    <input id="wait_selector" placeholder="e.g. article, .company-card" />
  </div>

  <div>
    <div class="section-label">Extraction Mode</div>
    <select id="mode">
      <option value="auto">🔍 Auto Detect</option>
      <option value="table">📊 HTML Tables</option>
      <option value="cards">🃏 Cards / Articles</option>
      <option value="json">{ } JSON API</option>
      <option value="custom">✏️ Custom Selectors</option>
    </select>
  </div>

  <div id="selector-section">
    <div class="section-label">CSS Selector <span style="font-size:10px;color:var(--muted)">(optional override)</span></div>
    <input id="selector" placeholder="e.g. article.company-card" />
  </div>

  <div id="fieldmap-section" style="display:none">
    <div class="section-label">Field Map <span style="font-size:10px;color:var(--muted)">name → CSS selector</span></div>
    <div id="field-rows"></div>
    <button class="btn btn-sm" style="width:auto;margin-top:4px" onclick="addFieldRow()">+ Add Field</button>
  </div>

  <div>
    <div class="section-label">Pagination</div>
    <div class="row2">
      <div><label>Max Pages</label><input type="number" id="max_pages" value="5" min="1" max="500" /></div>
      <div><label>Delay (sec)</label><input type="number" id="delay" value="1.5" min="0" max="60" step="0.5" /></div>
    </div>
  </div>

  <div>
    <div class="section-label">Reliability</div>
    <div class="row2">
      <div><label>Retries</label><input type="number" id="retries" value="3" min="1" max="10" /></div>
      <div><label>Timeout (sec)</label><input type="number" id="timeout" value="30" min="5" max="120" /></div>
    </div>
  </div>

  <div>
    <div class="section-label">Advanced</div>
    <label>Proxy</label>
    <input id="proxy" placeholder="http://user:pass@host:port" style="margin-bottom:7px" />
    <label>Auth Token</label>
    <input id="auth_token" type="password" placeholder="Bearer token / API key" />
  </div>

  <button class="btn btn-primary" id="scrape-btn" onclick="startScrape()">▶ Start Scraping</button>
  <button class="btn btn-stop"    id="stop-btn"   onclick="stopScrape()" style="display:none">⏹ Stop</button>

</div>

<div class="main">

  <div class="toolbar">
    <input class="search-box" id="search" placeholder="Search results…" oninput="renderTable()" />
    <div style="flex:1"></div>
    <button class="btn btn-sm" onclick="exportData('csv')">⬇ CSV</button>
    <button class="btn btn-sm" onclick="exportData('json')">⬇ JSON</button>
    <button class="btn btn-sm" onclick="exportData('excel')">⬇ Excel</button>
    <button class="btn btn-clear btn" onclick="clearData()">🗑 Clear</button>
  </div>

  <div class="status-bar">
    <span class="status-pill idle" id="status-pill"><span class="dot"></span>&nbsp;Idle</span>
    <span id="status-msg">Ready.</span>
    <span id="fetch-tag-display"></span>
    <div style="flex:1"></div>
    <span id="row-count" style="font-weight:700;font-size:13px"></span>
  </div>

  <div class="progress-bar-wrap"><div class="progress-bar" id="progress-bar" style="width:0%"></div></div>
  <div class="log-panel" id="log"></div>

  <div class="table-wrap" id="table-wrap">
    <div class="empty-state">
      <div class="empty-icon">🕸</div>
      <div>Configure a URL and hit Start Scraping</div>
    </div>
  </div>

</div>
</div>

<script>
let scrapedData = [];
let stopFlag = false;

function setPill(el) {
  document.querySelectorAll('.fetch-pill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('wait-sel-row').style.display =
    el.dataset.val === 'browser' ? 'block' : 'none';
}
function getFetchMode() {
  return document.querySelector('.fetch-pill.active')?.dataset.val || 'auto';
}

document.getElementById('mode').addEventListener('change', function() {
  const isCustom = this.value === 'custom';
  document.getElementById('fieldmap-section').style.display = isCustom ? 'block' : 'none';
  document.getElementById('selector-section').style.display = isCustom ? 'none' : 'block';
  if (isCustom && !document.querySelectorAll('.fm-name').length) {
    addFieldRow('name',        'h2, h3, h4');
    addFieldRow('description', 'p');
    addFieldRow('url',         'a[href]');
  }
});

function addFieldRow(name='', sel='') {
  const c = document.getElementById('field-rows');
  const d = document.createElement('div');
  d.className = 'field-map-row';
  d.innerHTML = `<input placeholder="field name" value="${name}" class="fm-name">
                 <input placeholder="CSS selector" value="${sel}" class="fm-sel">
                 <button class="icon-btn" onclick="this.parentElement.remove()">×</button>`;
  c.appendChild(d);
}

function getFieldMap() {
  const map = {};
  document.querySelectorAll('.field-map-row').forEach(r => {
    const n = r.querySelector('.fm-name').value.trim();
    const s = r.querySelector('.fm-sel').value.trim();
    if (n && s) map[n] = s;
  });
  return map;
}

function setStatus(state, msg) {
  const pill = document.getElementById('status-pill');
  pill.className = 'status-pill ' + state;
  const labels = { idle:'Idle', running:'Running', done:'Done', error:'Error' };
  pill.innerHTML = `<span class="dot${state==='running'?' pulse':''}"></span>&nbsp;${labels[state]||state}`;
  document.getElementById('status-msg').textContent = msg;
}

function showFetchTag(tag) {
  const el = document.getElementById('fetch-tag-display');
  el.innerHTML = tag ? `<span class="fetch-tag ${tag}">${tag}</span>` : '';
}

function addLog(msg, cls='') {
  const panel = document.getElementById('log');
  const d = document.createElement('div');
  d.className = 'log-entry' + (cls ? ' '+cls : '');
  d.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  panel.appendChild(d);
  panel.scrollTop = panel.scrollHeight;
}

function startScrape() {
  const url = document.getElementById('url').value.trim();
  if (!url) { alert('Please enter a URL.'); return; }

  stopFlag = false;
  scrapedData = [];
  renderTable();
  document.getElementById('log').innerHTML = '';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('fetch-tag-display').innerHTML = '';

  const config = {
    url,
    max_pages:     +document.getElementById('max_pages').value,
    delay:         +document.getElementById('delay').value,
    mode:          document.getElementById('mode').value,
    selector:      document.getElementById('selector').value.trim(),
    field_map:     getFieldMap(),
    proxy:         document.getElementById('proxy').value.trim(),
    auth_token:    document.getElementById('auth_token').value.trim(),
    retries:       +document.getElementById('retries').value,
    timeout:       +document.getElementById('timeout').value,
    fetch_mode:    getFetchMode(),
    wait_selector: document.getElementById('wait_selector').value.trim(),
  };

  document.getElementById('scrape-btn').disabled = true;
  document.getElementById('stop-btn').style.display = 'block';
  setStatus('running', `Scraping ${url}…`);
  addLog(`▶ [${config.fetch_mode}] ${url}`, 'ok');

  fetch('/scrape', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config)
  }).then(resp => {
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    function read() {
      if (stopFlag) { finishScrape(); return; }
      reader.read().then(({ done, value }) => {
        if (done) { finishScrape(); return; }
        buf += dec.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop();
        parts.forEach(part => {
          if (!part.startsWith('data:')) return;
          try { handleEvent(JSON.parse(part.slice(5).trim()), config.max_pages); }
          catch(e) {}
        });
        read();
      }).catch(() => finishScrape());
    }
    read();
  }).catch(err => {
    addLog('Connection error: ' + err.message, 'err');
    setStatus('error', 'Connection failed.');
    finishScrape();
  });
}

function handleEvent(ev, maxPages) {
  if (ev.type === 'progress') {
    setStatus('running', `Page ${ev.page} of ${maxPages}…`);
    addLog(`→ Page ${ev.page}: ${ev.url}`);
    document.getElementById('progress-bar').style.width =
      Math.min(100, (ev.page / maxPages) * 100) + '%';
  }
  else if (ev.type === 'page_done') {
    showFetchTag(ev.fetch || '');
    addLog(`✓ Page ${ev.page}: ${ev.rows} rows  [${ev.mode||'json'} · ${ev.fetch||''}]`, 'ok');
  }
  else if (ev.type === 'info') {
    addLog('ℹ ' + ev.msg, 'info');
  }
  else if (ev.type === 'error') {
    addLog('✗ ' + ev.msg, 'err');
    setStatus('error', ev.msg);
  }
  else if (ev.type === 'done') {
    scrapedData = ev.data;
    renderTable();
    document.getElementById('progress-bar').style.width = '100%';
    document.getElementById('row-count').textContent = `${ev.total} rows`;
    addLog(`✓ Done — ${ev.total} rows total.`, 'ok');
    setStatus('done', `${ev.total} rows scraped.`);
    finishScrape();
  }
}

function finishScrape() {
  document.getElementById('scrape-btn').disabled = false;
  document.getElementById('stop-btn').style.display = 'none';
  const pill = document.getElementById('status-pill');
  if (pill.classList.contains('running'))
    setStatus('idle', 'No data found — try Browser mode.');
}

function stopScrape() {
  stopFlag = true;
  addLog('⏹ Stopped by user.', 'warn');
  setStatus('idle', 'Stopped.');
  finishScrape();
}

function clearData() {
  scrapedData = [];
  renderTable();
  document.getElementById('row-count').textContent = '';
  document.getElementById('log').innerHTML = '';
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('fetch-tag-display').innerHTML = '';
  setStatus('idle', 'Ready.');
}

function renderTable() {
  const wrap = document.getElementById('table-wrap');
  const q = document.getElementById('search').value.toLowerCase();
  let data = scrapedData;
  if (q) data = data.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)));

  if (!data.length) {
    wrap.innerHTML = `<div class="empty-state">
      <div class="empty-icon">🕸</div>
      <div>${scrapedData.length ? 'No results match your search.' : 'Configure a URL and hit Start Scraping'}</div>
    </div>`;
    return;
  }

  const cols = [...new Set(data.flatMap(r => Object.keys(r)))];
  const baseHost = (() => {
    try { return new URL(document.getElementById('url').value).origin; } catch { return ''; }
  })();

  const hdr = cols.map(c => `<th>${c}</th>`).join('');
  const bdy = data.slice(0, 3000).map(row =>
    `<tr>${cols.map(c => {
      const v = row[c] ?? '';
      const sv = String(v);
      const isLink = /^https?:\/\//.test(sv) || sv.startsWith('/');
      if (isLink) {
        const href = sv.startsWith('/') ? baseHost + sv : sv;
        return `<td><a href="${href}" target="_blank" title="${sv}">${sv.slice(0,55)}${sv.length>55?'…':''}</a></td>`;
      }
      return `<td title="${sv.replace(/"/g,'&quot;')}">${sv.slice(0,80)}</td>`;
    }).join('')}</tr>`
  ).join('');

  wrap.innerHTML = `<table><thead><tr>${hdr}</tr></thead><tbody>${bdy}</tbody></table>`;
  document.getElementById('row-count').textContent =
    `${data.length}${data.length < scrapedData.length ? ' / '+scrapedData.length : ''} rows`;
}

function exportData(fmt) {
  if (!scrapedData.length) { alert('No data to export yet.'); return; }
  fetch('/export/' + fmt, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ data: scrapedData })
  }).then(r => r.blob()).then(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `scraped_data.${fmt === 'excel' ? 'xlsx' : fmt}`;
    a.click();
  });
}
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("\n🕸  Universal Web Scraper (TLS Bypass & Stealth Edition)")
    print("   Install deps:")
    print("     pip install flask pandas beautifulsoup4 requests lxml curl_cffi openpyxl playwright playwright-stealth")
    print("     playwright install chromium")
    print("   Run  → python universal_scraper.py")
    print("   Open → http://localhost:5000\n")
    app.run(debug=True, port=5000, threaded=True)