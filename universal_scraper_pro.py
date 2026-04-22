#!/usr/bin/env python3
"""
Universal Web Scraper PRO - Enhanced Features
Additions: Scheduling, Caching, Filtering, API, SQLite, Rate Limiting, Selector Testing
"""

import os
import re
import json
import time
import logging
import sqlite3
import hashlib
import threading
from io import StringIO
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse, urlencode, urlunparse, parse_qs
from pathlib import Path

import requests
import pandas as pd
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_file, Response, stream_with_context
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ── logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s │ %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
scheduler = BackgroundScheduler()

# ── database ─────────────────────────────────────────────────────────────────

class Database:
    def __init__(self, db_path: str = "scraper.db"):
        self.db_path = db_path
        self.init_db()

    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_conn()
        c = conn.cursor()
        
        # Cache table
        c.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                url TEXT PRIMARY KEY,
                content_hash TEXT,
                data TEXT,
                timestamp DATETIME,
                expires DATETIME
            )
        """)
        
        # Scrape history
        c.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY,
                url TEXT,
                mode TEXT,
                rows_count INTEGER,
                status TEXT,
                created_at DATETIME,
                data TEXT
            )
        """)
        
        # Scheduled jobs
        c.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                url TEXT,
                config TEXT,
                schedule TEXT,
                enabled BOOLEAN,
                last_run DATETIME,
                next_run DATETIME
            )
        """)
        
        conn.commit()
        conn.close()

    def get_cached(self, url: str) -> dict | None:
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT data FROM cache WHERE url=? AND expires > datetime('now')",
            (url,)
        )
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return None

    def set_cache(self, url: str, data: dict, ttl_hours: int = 24):
        conn = self.get_conn()
        c = conn.cursor()
        h = hashlib.md5(json.dumps(data).encode()).hexdigest()
        expires = datetime.now() + timedelta(hours=ttl_hours)
        c.execute(
            """INSERT OR REPLACE INTO cache (url, content_hash, data, timestamp, expires)
               VALUES (?, ?, ?, datetime('now'), ?)""",
            (url, h, json.dumps(data), expires)
        )
        conn.commit()
        conn.close()

    def add_history(self, url: str, mode: str, rows: int, status: str, data: list):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT INTO history (url, mode, rows_count, status, created_at, data)
               VALUES (?, ?, ?, ?, datetime('now'), ?)""",
            (url, mode, rows, status, json.dumps(data[:100]))  # Store first 100 rows
        )
        conn.commit()
        conn.close()

    def save_job(self, name: str, url: str, config: dict, schedule: str):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute(
            """INSERT OR REPLACE INTO jobs (name, url, config, schedule, enabled, next_run)
               VALUES (?, ?, ?, ?, 1, datetime('now'))""",
            (name, url, json.dumps(config), schedule)
        )
        conn.commit()
        conn.close()

    def get_jobs(self):
        conn = self.get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM jobs WHERE enabled=1")
        jobs = [dict(row) for row in c.fetchall()]
        conn.close()
        return jobs

db = Database()

# ── helpers ───────────────────────────────────────────────────────────────────

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

class RateLimiter:
    def __init__(self, requests_per_second: float = 1.0):
        self.requests_per_second = requests_per_second
        self.min_interval = 1.0 / requests_per_second
        self.last_request_time = 0

    def wait(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request_time = time.time()

rate_limiter = RateLimiter(requests_per_second=1.0)

def build_session(proxy: str = "", auth_token: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    if auth_token:
        s.headers["Authorization"] = f"Bearer {auth_token}"
    return s

def fetch(session: requests.Session, url: str, retries: int = 3,
          delay: float = 1.0, timeout: int = 15) -> requests.Response | None:
    rate_limiter.wait()
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay * attempt)
    return None

# ── detection ─────────────────────────────────────────────────────────────────

def detect_content_type(soup: BeautifulSoup, url: str) -> str:
    """Auto-detect content type."""
    ct = soup.find("pre")
    if ct:
        try:
            json.loads(ct.get_text())
            return "json"
        except ValueError:
            pass

    if soup.find("table"):
        return "table"

    for tag in ("article", "li", "div"):
        elems = soup.find_all(tag)
        if len(elems) >= 3:
            texts = [e.get_text(strip=True) for e in elems[:5]]
            if all(len(t) > 20 for t in texts):
                return "cards"

    return "table"

def detect_next_page(soup: BeautifulSoup, current_url: str) -> str | None:
    """Detect next page URL."""
    nxt = soup.find("a", rel=lambda r: r and "next" in r)
    if nxt and nxt.get("href"):
        return urljoin(current_url, nxt["href"])

    for a in soup.find_all("a"):
        txt = a.get_text(strip=True).lower()
        if txt in ("next", "next »", "»", "›", ">", "next page"):
            href = a.get("href")
            if href:
                return urljoin(current_url, href)

    parsed = urlparse(current_url)
    qs = parse_qs(parsed.query)
    for key in ("page", "p", "pg", "offset"):
        if key in qs:
            try:
                val = int(qs[key][0])
                qs[key] = [str(val + 1)]
                new_qs = urlencode({k: v[0] for k, v in qs.items()})
                return urlunparse(parsed._replace(query=new_qs))
            except ValueError:
                pass

    return None

# ── extractors ────────────────────────────────────────────────────────────────

def extract_table(soup: BeautifulSoup, selector: str = "") -> list[dict]:
    rows = []
    tables = soup.select(selector) if selector else soup.find_all("table")
    if not tables:
        return rows

    for table in tables:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        if not headers:
            first_row = table.find("tr")
            if first_row:
                headers = [td.get_text(strip=True) for td in first_row.find_all("td")]
                all_rows = table.find_all("tr")[1:]
            else:
                continue
        else:
            all_rows = table.find_all("tr")[1:]

        if not headers:
            continue

        for tr in all_rows:
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
            rows.append(row)

    return rows

def extract_cards(soup: BeautifulSoup, selector: str = "") -> list[dict]:
    rows = []
    candidates = []

    if selector:
        candidates = soup.select(selector)
    else:
        for tag in ("article", "li", "div"):
            groups = soup.find_all(tag)
            for g in groups:
                siblings = [s for s in g.parent.children
                            if hasattr(s, "name") and s.name == tag]
                if len(siblings) >= 3:
                    candidates = siblings
                    break
            if candidates:
                break

    for elem in candidates:
        data = {}
        for h in ("h1", "h2", "h3", "h4"):
            t = elem.find(h)
            if t:
                data["title"] = t.get_text(strip=True)
                break

        a = elem.find("a")
        if a:
            data.setdefault("title", a.get_text(strip=True))
            data["url"] = a.get("href", "")

        p = elem.find("p")
        if p:
            data["description"] = p.get_text(strip=True)

        nums = re.findall(r"\b[\$€£]?[\d,]+\.?\d*\b", elem.get_text())
        if nums:
            data["value"] = nums[0]

        if data:
            rows.append(data)

    return rows

def extract_json_api(response: requests.Response) -> list[dict]:
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
    """Extract using custom CSS selectors."""
    if not field_map:
        return []

    anchor_sel, anchor_count = "", 0
    for sel in field_map.values():
        n = len(soup.select(sel))
        if n > anchor_count:
            anchor_count, anchor_sel = n, sel

    if not anchor_count:
        return []

    anchors = soup.select(anchor_sel)
    rows = []
    for _ in anchors:
        row = {}
        for name, sel in field_map.items():
            elems = soup.select(sel)
            if elems:
                row[name] = elems[0].get_text(strip=True)
                elems.pop(0)
        if any(row.values()):
            rows.append(row)

    return rows

# ── data transformations ──────────────────────────────────────────────────────

def apply_filters(data: list, filters: dict) -> list:
    """Apply filters to data."""
    if not filters:
        return data

    result = data
    for field, condition in filters.items():
        if isinstance(condition, dict):
            if 'min' in condition:
                try:
                    result = [d for d in result if float(d.get(field, 0)) >= condition['min']]
                except (ValueError, TypeError):
                    pass
            if 'max' in condition:
                try:
                    result = [d for d in result if float(d.get(field, 0)) <= condition['max']]
                except (ValueError, TypeError):
                    pass
            if 'contains' in condition:
                result = [d for d in result if str(condition['contains']).lower() in str(d.get(field, '')).lower()]
        else:
            result = [d for d in result if d.get(field) == condition]

    return result

def apply_transformations(data: list, transforms: dict) -> list:
    """Apply transformations (rename, calculate, etc)."""
    if not transforms:
        return data

    for row in data:
        for old_key, new_key in transforms.items():
            if old_key in row:
                row[new_key] = row.pop(old_key)

    return data

# ── main scrape logic ─────────────────────────────────────────────────────────

def scrape(config: dict):
    """Main scraping generator."""
    url = config["url"]
    max_pages = int(config.get("max_pages", 1))
    delay = float(config.get("delay", 1.0))
    mode = config.get("mode", "auto")
    selector = config.get("selector", "")
    field_map = config.get("field_map", {})
    proxy = config.get("proxy", "")
    auth_token = config.get("auth_token", "")
    retries = int(config.get("retries", 3))
    timeout = int(config.get("timeout", 15))
    use_cache = config.get("use_cache", True)
    filters = config.get("filters", {})
    transforms = config.get("transforms", {})

    session = build_session(proxy, auth_token)
    all_rows: list[dict] = []
    page = 1
    current_url = url

    while page <= max_pages and current_url:
        yield {"type": "progress", "page": page, "url": current_url, "count": len(all_rows)}

        # Check cache
        if use_cache:
            cached = db.get_cached(current_url)
            if cached:
                all_rows.extend(cached)
                yield {"type": "page_done", "page": page, "rows": len(cached), "cached": True}
                page += 1
                time.sleep(delay)
                continue

        resp = fetch(session, current_url, retries=retries, delay=delay, timeout=timeout)
        if not resp:
            yield {"type": "error", "msg": f"Failed to fetch page {page}"}
            break

        ct_header = resp.headers.get("Content-Type", "")
        if "json" in ct_header or mode == "json":
            rows = extract_json_api(resp)
            all_rows.extend(rows)
            if use_cache:
                db.set_cache(current_url, rows)
            yield {"type": "page_done", "page": page, "rows": len(rows)}
            break

        soup = BeautifulSoup(resp.text, "lxml")

        if mode == "auto":
            mode_used = detect_content_type(soup, current_url)
        else:
            mode_used = mode

        if mode_used == "table":
            rows = extract_table(soup, selector)
        elif mode_used == "cards":
            rows = extract_cards(soup, selector)
        elif mode_used == "custom":
            rows = extract_custom(soup, field_map)
        else:
            rows = extract_table(soup, selector) or extract_cards(soup, selector)

        # Apply filters and transforms
        rows = apply_filters(rows, filters)
        rows = apply_transformations(rows, transforms)

        all_rows.extend(rows)
        if use_cache:
            db.set_cache(current_url, rows)
        yield {"type": "page_done", "page": page, "rows": len(rows), "mode": mode_used}

        if page >= max_pages:
            break

        next_url = detect_next_page(soup, current_url)
        if not next_url or next_url == current_url:
            yield {"type": "info", "msg": "No further pages detected."}
            break

        current_url = next_url
        page += 1
        time.sleep(delay)

    # Save to history
    db.add_history(url, mode, len(all_rows), "success", all_rows)
    yield {"type": "done", "total": len(all_rows), "data": all_rows}

# ── scheduled scraping ────────────────────────────────────────────────────────

def run_scheduled_scrape(job_id: int, url: str, config: dict):
    """Run scrape from scheduler."""
    log.info(f"Running scheduled scrape: {url}")
    results = list(scrape({"url": url, **config}))
    if results and results[-1]["type"] == "done":
        log.info(f"Scheduled scrape completed: {results[-1]['total']} rows")

# ── Flask routes ──────────────────────────────────────────────────────────────

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

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """API endpoint for programmatic scraping."""
    config = request.get_json(force=True)
    results = []
    for event in scrape(config):
        if event["type"] == "done":
            results = event["data"]
    return jsonify({"status": "success", "data": results})

@app.route("/test-selector", methods=["POST"])
def test_selector():
    """Test CSS selector against URL."""
    data = request.get_json(force=True)
    url = data.get("url")
    selector = data.get("selector")
    
    try:
        resp = fetch(build_session(), url, retries=1)
        if not resp:
            return jsonify({"error": "Failed to fetch URL"}), 400
        
        soup = BeautifulSoup(resp.text, "lxml")
        elements = soup.select(selector)
        results = [{"text": e.get_text(strip=True)[:100], "html": str(e)[:200]} for e in elements[:10]]
        return jsonify({"matches": len(elements), "samples": results})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/history", methods=["GET"])
def get_history():
    """Get scrape history."""
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM history ORDER BY created_at DESC LIMIT 50")
    history = [dict(row) for row in c.fetchall()]
    conn.close()
    return jsonify(history)

@app.route("/cache/clear", methods=["POST"])
def clear_cache():
    """Clear cache."""
    conn = db.get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM cache")
    conn.commit()
    conn.close()
    return jsonify({"status": "cache cleared"})

@app.route("/jobs", methods=["GET"])
def get_jobs():
    """Get scheduled jobs."""
    jobs = db.get_jobs()
    return jsonify(jobs)

@app.route("/jobs/add", methods=["POST"])
def add_job():
    """Add scheduled job."""
    data = request.get_json(force=True)
    name = data.get("name")
    url = data.get("url")
    config = data.get("config", {})
    schedule = data.get("schedule")  # e.g., "0 9 * * *" (9 AM daily)
    
    db.save_job(name, url, config, schedule)
    
    # Add to scheduler
    try:
        scheduler.add_job(
            run_scheduled_scrape,
            CronTrigger.from_crontab(schedule),
            args=[0, url, config],
            id=name,
            replace_existing=True
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
    return jsonify({"status": "job added", "name": name})

@app.route("/export/<fmt>", methods=["POST"])
def export(fmt: str):
    data = request.get_json(force=True).get("data", [])
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
        import tempfile
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        df.to_excel(tmp.name, index=False)
        return send_file(tmp.name, as_attachment=True, download_name="scraped_data.xlsx")

    return jsonify({"error": "Unknown format"}), 400

# ── frontend html ─────────────────────────────────────────────────────────────

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Universal Web Scraper PRO</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0e1117;
    --surface: #161b27;
    --surface2: #1e2535;
    --border: #2a3047;
    --accent: #4f8ef7;
    --accent2: #6c63ff;
    --success: #22d3a4;
    --warning: #f5a623;
    --danger: #f05252;
    --text: #e8ecf4;
    --muted: #7a8394;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }

  header {
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 12px;
    background: var(--surface);
  }

  .logo { font-size: 20px; font-weight: 700; }
  .badge { font-size: 11px; background: var(--accent2); color: #fff; padding: 2px 8px; border-radius: 20px; }
  .pro-badge { background: var(--success); }

  .layout { display: grid; grid-template-columns: 1fr; gap: 12px; padding: 12px; }

  .tabs {
    display: flex;
    gap: 4px;
    border-bottom: 1px solid var(--border);
    padding: 0 12px;
  }

  .tab {
    padding: 10px 16px;
    cursor: pointer;
    border: none;
    background: none;
    color: var(--muted);
    border-bottom: 2px solid transparent;
  }

  .tab.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .content { display: none; }
  .content.active { display: block; }

  .section {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px;
    margin-bottom: 12px;
  }

  .section-label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--muted);
    margin-bottom: 8px;
    font-weight: 600;
  }

  label { font-size: 13px; color: var(--muted); display: block; margin-bottom: 4px; }

  input, select, textarea {
    width: 100%;
    background: var(--surface2);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 8px 10px;
    font-size: 13px;
    outline: none;
    transition: border-color 0.15s;
  }
  input:focus, select:focus { border-color: var(--accent); }

  .grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; }
  .grid-2 { grid-template-columns: 1fr 1fr; }

  .btn {
    padding: 9px 16px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    border: none;
    transition: all 0.15s;
  }

  .btn-primary { background: var(--accent); color: #fff; width: 100%; }
  .btn-primary:hover { background: #3a7af0; }
  .btn-sm { background: var(--surface2); color: var(--text); border: 1px solid var(--border); padding: 6px 12px; }
  .btn-sm:hover { border-color: var(--accent); color: var(--accent); }

  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    background: rgba(79,142,247,0.15);
    color: var(--accent);
  }

  .status-pill.success { background: rgba(34,211,164,0.15); color: var(--success); }
  .status-pill.error { background: rgba(240,82,82,0.15); color: var(--danger); }

  table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 12px; }
  th {
    background: var(--surface2);
    padding: 8px 12px;
    text-align: left;
    font-weight: 600;
    color: var(--muted);
    border-bottom: 1px solid var(--border);
  }
  td { padding: 8px 12px; border-bottom: 1px solid var(--border); }
  tr:hover td { background: rgba(255,255,255,0.02); }

  .grid-field {
    display: grid;
    grid-template-columns: 1fr 1fr 28px;
    gap: 8px;
    align-items: center;
    margin-bottom: 8px;
  }

  .icon-btn { background: none; border: none; color: var(--danger); cursor: pointer; padding: 0; }
</style>
</head>
<body>

<header>
  <div class="logo">Web<span style="color:var(--accent)">Scraper</span></div>
  <div class="badge pro-badge">PRO</div>
</header>

<div class="tabs">
  <button class="tab active" onclick="showTab('scraper')">🕷️ Scraper</button>
  <button class="tab" onclick="showTab('filters')">🔍 Filters & Transform</button>
  <button class="tab" onclick="showTab('selector-tester')">🧪 Selector Tester</button>
  <button class="tab" onclick="showTab('schedule')">⏰ Schedule</button>
  <button class="tab" onclick="showTab('history')">📊 History</button>
  <button class="tab" onclick="showTab('cache')">💾 Cache</button>
</div>

<div class="layout">

  <!-- SCRAPER TAB -->
  <div id="scraper" class="content active">
    <div class="section">
      <div class="section-label">Target URL</div>
      <input type="url" id="url" placeholder="https://example.com" />
    </div>

    <div class="grid-2">
      <div class="section">
        <div class="section-label">Extraction Mode</div>
        <select id="mode">
          <option value="auto">🔍 Auto Detect</option>
          <option value="table">📊 Tables</option>
          <option value="cards">🃏 Cards</option>
          <option value="json">{ } JSON API</option>
          <option value="custom">✏️ Custom</option>
        </select>
      </div>

      <div class="section">
        <div class="section-label">Pagination</div>
        <input type="number" id="max_pages" value="5" min="1" max="500" />
      </div>
    </div>

    <div class="grid-2">
      <div class="section">
        <div class="section-label">Delay (sec)</div>
        <input type="number" id="delay" value="1" min="0" max="30" step="0.5" />
      </div>

      <div class="section">
        <div class="section-label">Retries</div>
        <input type="number" id="retries" value="3" min="1" max="10" />
      </div>
    </div>

    <div class="grid-2">
      <div class="section">
        <label><input type="checkbox" id="use_cache" checked /> Use Cache</label>
      </div>

      <div class="section">
        <div class="section-label">Proxy (optional)</div>
        <input id="proxy" placeholder="http://host:port" />
      </div>
    </div>

    <div style="margin-top: 12px;">
      <button class="btn btn-primary" onclick="startScrape()">▶ Start Scraping</button>
      <button class="btn btn-sm" onclick="exportData('csv')" style="margin-top: 8px; width: 32%;">⬇ CSV</button>
      <button class="btn btn-sm" onclick="exportData('json')" style="margin-top: 8px; width: 32%; margin-left: 2%;">⬇ JSON</button>
      <button class="btn btn-sm" onclick="exportData('excel')" style="margin-top: 8px; width: 32%; margin-left: 2%;">⬇ Excel</button>
    </div>

    <div class="section" style="margin-top: 12px; max-height: 200px; overflow-y: auto;">
      <div id="log" style="font-family: monospace; font-size: 11px;"></div>
    </div>

    <div class="section" style="margin-top: 12px; max-height: 400px; overflow: auto;">
      <div id="results-table"></div>
    </div>
  </div>

  <!-- FILTERS TAB -->
  <div id="filters" class="content">
    <div class="section">
      <div class="section-label">Filters</div>
      <p style="color: var(--muted); margin-bottom: 12px; font-size: 12px;">Add conditions to filter results</p>
      <div id="filters-container"></div>
      <button class="btn btn-sm" onclick="addFilter()" style="margin-top: 8px;">+ Add Filter</button>
    </div>

    <div class="section">
      <div class="section-label">Transformations (Rename Fields)</div>
      <div id="transforms-container"></div>
      <button class="btn btn-sm" onclick="addTransform()" style="margin-top: 8px;">+ Add Transform</button>
    </div>
  </div>

  <!-- SELECTOR TESTER TAB -->
  <div id="selector-tester" class="content">
    <div class="section">
      <div class="section-label">Test CSS Selector</div>
      <label>URL</label>
      <input type="url" id="test-url" placeholder="https://example.com" style="margin-bottom: 8px;" />
      
      <label>CSS Selector</label>
      <input id="test-selector" placeholder="e.g., div.item, table.data" style="margin-bottom: 8px;" />
      
      <button class="btn btn-primary" onclick="testSelector()">🧪 Test</button>
      <div id="selector-results" style="margin-top: 12px;"></div>
    </div>
  </div>

  <!-- SCHEDULE TAB -->
  <div id="schedule" class="content">
    <div class="section">
      <div class="section-label">Create Scheduled Job</div>
      <input type="text" id="job-name" placeholder="Job Name" style="margin-bottom: 8px;" />
      <input type="url" id="job-url" placeholder="URL" style="margin-bottom: 8px;" />
      <input type="text" id="job-schedule" placeholder="Cron: 0 9 * * * (daily 9 AM)" style="margin-bottom: 8px;" />
      <button class="btn btn-primary" onclick="createJob()">+ Schedule</button>
      
      <div id="jobs-list" style="margin-top: 12px;"></div>
    </div>
  </div>

  <!-- HISTORY TAB -->
  <div id="history" class="content">
    <div class="section">
      <div class="section-label">Scrape History</div>
      <button class="btn btn-sm" onclick="loadHistory()">🔄 Refresh</button>
      <div id="history-list" style="margin-top: 12px;"></div>
    </div>
  </div>

  <!-- CACHE TAB -->
  <div id="cache" class="content">
    <div class="section">
      <div class="section-label">Cache Management</div>
      <button class="btn btn-primary" onclick="clearCache()">🗑 Clear Cache</button>
      <p style="color: var(--success); margin-top: 12px;" id="cache-status"></p>
    </div>
  </div>

</div>

<script>
let scrapedData = [];

// TAB MANAGEMENT
function showTab(tab) {
  document.querySelectorAll('.content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById(tab).classList.add('active');
  event.target.classList.add('active');
}

// FILTERS & TRANSFORMS
function addFilter() {
  const div = document.createElement('div');
  div.className = 'grid-field';
  div.innerHTML = `
    <input placeholder="Field name" class="filter-field" />
    <input placeholder="Value / Min / Max" class="filter-value" />
    <button class="icon-btn" onclick="this.parentElement.remove()">×</button>
  `;
  document.getElementById('filters-container').appendChild(div);
}

function addTransform() {
  const div = document.createElement('div');
  div.className = 'grid-field';
  div.innerHTML = `
    <input placeholder="Old name" class="transform-old" />
    <input placeholder="New name" class="transform-new" />
    <button class="icon-btn" onclick="this.parentElement.remove()">×</button>
  `;
  document.getElementById('transforms-container').appendChild(div);
}

function getFilters() {
  const filters = {};
  document.querySelectorAll('#filters-container .grid-field').forEach(row => {
    const field = row.querySelector('.filter-field').value.trim();
    const value = row.querySelector('.filter-value').value.trim();
    if (field && value) filters[field] = value;
  });
  return filters;
}

function getTransforms() {
  const transforms = {};
  document.querySelectorAll('#transforms-container .grid-field').forEach(row => {
    const old = row.querySelector('.transform-old').value.trim();
    const neo = row.querySelector('.transform-new').value.trim();
    if (old && neo) transforms[old] = neo;
  });
  return transforms;
}

// SCRAPING
function startScrape() {
  const url = document.getElementById('url').value.trim();
  if (!url) { alert('Enter URL'); return; }

  scrapedData = [];
  document.getElementById('log').innerHTML = '';
  document.getElementById('results-table').innerHTML = '';

  const config = {
    url,
    max_pages: +document.getElementById('max_pages').value,
    delay: +document.getElementById('delay').value,
    mode: document.getElementById('mode').value,
    retries: +document.getElementById('retries').value,
    use_cache: document.getElementById('use_cache').checked,
    proxy: document.getElementById('proxy').value.trim(),
    filters: getFilters(),
    transforms: getTransforms(),
  };

  fetch('/scrape', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(config) })
    .then(resp => resp.body.getReader())
    .then(reader => {
      const decoder = new TextDecoder();
      let buf = '';
      function read() {
        reader.read().then(({done, value}) => {
          if (done) return;
          buf += decoder.decode(value, {stream: true});
          buf.split('\n\n').slice(0, -1).forEach(part => {
            if (part.startsWith('data:')) {
              const ev = JSON.parse(part.slice(5));
              handleEvent(ev);
            }
          });
          read();
        });
      }
      read();
    });
}

function handleEvent(ev) {
  if (ev.type === 'done') {
    scrapedData = ev.data;
    renderResults();
    addLog(`✓ Done: ${ev.total} rows`, 'success');
  }
  else if (ev.type === 'page_done') {
    addLog(`✓ Page ${ev.page}: ${ev.rows} rows${ev.cached?' (cached)':''}`);
  }
  else if (ev.type === 'error') {
    addLog(`✗ ${ev.msg}`, 'error');
  }
}

function addLog(msg, cls='') {
  const log = document.getElementById('log');
  const p = document.createElement('p');
  p.className = cls;
  p.style.color = cls === 'success' ? 'var(--success)' : cls === 'error' ? 'var(--danger)' : 'var(--muted)';
  p.textContent = `[${new Date().toLocaleTimeString()}] ${msg}`;
  log.appendChild(p);
  log.scrollTop = log.scrollHeight;
}

function renderResults() {
  if (!scrapedData.length) return;
  const cols = [...new Set(scrapedData.flatMap(r => Object.keys(r)))];
  const html = `
    <table>
      <thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr></thead>
      <tbody>${scrapedData.slice(0, 100).map(row => 
        `<tr>${cols.map(c => `<td>${String(row[c] || '').slice(0, 50)}</td>`).join('')}</tr>`
      ).join('')}</tbody>
    </table>
  `;
  document.getElementById('results-table').innerHTML = html;
}

// SELECTOR TESTER
function testSelector() {
  const url = document.getElementById('test-url').value.trim();
  const selector = document.getElementById('test-selector').value.trim();
  if (!url || !selector) { alert('Fill in URL and selector'); return; }

  fetch('/test-selector', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({url, selector})
  }).then(r => r.json()).then(data => {
    document.getElementById('selector-results').innerHTML = `
      <p><strong>${data.matches || 0} matches</strong></p>
      <ul>${(data.samples || []).map(s => `<li>${s.text}</li>`).join('')}</ul>
    `;
  }).catch(e => alert('Error: ' + e.message));
}

// SCHEDULING
function createJob() {
  const name = document.getElementById('job-name').value.trim();
  const url = document.getElementById('job-url').value.trim();
  const schedule = document.getElementById('job-schedule').value.trim();
  if (!name || !url || !schedule) { alert('Fill in all fields'); return; }

  fetch('/jobs/add', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, url, schedule, config: {max_pages: 5}})
  }).then(r => r.json()).then(data => {
    alert('Job scheduled: ' + data.name);
    document.getElementById('job-name').value = '';
    document.getElementById('job-url').value = '';
  }).catch(e => alert('Error: ' + e.message));
}

// HISTORY
function loadHistory() {
  fetch('/history').then(r => r.json()).then(history => {
    const html = `<table>
      <thead><tr><th>URL</th><th>Mode</th><th>Rows</th><th>Date</th></tr></thead>
      <tbody>${history.map(h => `
        <tr><td>${h.url.slice(0,40)}</td><td>${h.mode}</td><td>${h.rows_count}</td><td>${h.created_at}</td></tr>
      `).join('')}</tbody>
    </table>`;
    document.getElementById('history-list').innerHTML = html;
  });
}

// CACHE
function clearCache() {
  fetch('/cache/clear', {method: 'POST'}).then(() => {
    document.getElementById('cache-status').textContent = '✓ Cache cleared!';
  });
}

// EXPORT
function exportData(fmt) {
  if (!scrapedData.length) { alert('No data'); return; }
  fetch('/export/' + fmt, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({data: scrapedData})
  }).then(r => r.blob()).then(blob => {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `data.${fmt === 'excel' ? 'xlsx' : fmt}`;
    a.click();
  });
}
</script>

</body>
</html>
"""

# ── startup ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start scheduler
    scheduler.start()
    
    print("\n🕸️  Universal Web Scraper PRO")
    print("   Open → http://localhost:5000\n")
    print("✨ Features:")
    print("   • Auto-detection & custom selectors")
    print("   • Smart pagination")
    print("   • Filtering & data transformation") 
    print("   • SQLite caching")
    print("   • Scheduled scraping (cron)")
    print("   • Selector testing tool")
    print("   • Rate limiting & politeness")
    print("   • REST API (/api/scrape)")
    print("   • Export: CSV, JSON, Excel\n")
    
    try:
        app.run(debug=False, port=5000, threaded=True)
    except KeyboardInterrupt:
        scheduler.shutdown()
