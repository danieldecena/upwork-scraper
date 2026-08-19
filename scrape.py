#!/usr/bin/env python3
"""
Upwork job-search scraper.

Playwright/CDP automation gets stuck indefinitely on Upwork's Cloudflare
Turnstile challenge (confirmed: headed, headless, and real-Chrome-channel
all hang past a 30s wait) -- Cloudflare fingerprints the CDP automation
protocol itself, not just headless mode or the browser binary. Only a
real, already-authenticated browser session clears it. So this tool is
split in two instead of doing its own fetching:

  1. `--print-urls` builds the search URL(s) for a scenario/query -- an
     agent (or you) opens these in a real browser (e.g. the claude-in-chrome
     extension) and extracts each page's job tiles.
  2. `--ingest <file.json>` takes that extracted data (a JSON array of raw
     tile dicts) and normalizes + upserts it into the SQLite DB.

Setup (once):
    /opt/homebrew/bin/python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Usage:
    python scrape.py --print-urls --scenario quick-easy --pages 3
    python scrape.py --ingest page1.json --scenario quick-easy
    python scrape.py --print --scenario high-paying

Each ingested JSON file is a list of objects with the raw fields read off
a job tile (see README.md for the exact shape / extraction snippet):
    {"title", "path", "job_type_text", "fixed_price_text",
     "experience_level", "posted_text", "description", "skills": [...]}
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import yaml

BASE_URL = "https://www.upwork.com/nx/search/jobs/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_DB = SCRIPT_DIR / "jobs.db"
DEFAULT_SCENARIOS = SCRIPT_DIR / "scenarios.yaml"

DEFAULT_PARAMS = {
    "contractor_tier": "1,2,3",
    "duration_v3": "week,month",
    "per_page": 50,
    "location": "United States",
    "sort": "recency",
}


def build_url(params, page):
    q = {
        "q": params["query"],
        "sort": params.get("sort", "recency"),
        "contractor_tier": params.get("contractor_tier", "1,2,3"),
        "duration_v3": params.get("duration_v3", "week,month"),
        "per_page": params.get("per_page", 50),
        "location": params.get("location", "United States"),
        "t": 1,
    }
    if params.get("workload"):
        q["workload"] = params["workload"]
    if page > 1:
        q["page"] = page
    return f"{BASE_URL}?{urlencode(q)}"


def parse_budget(job_type_text, fixed_price_text):
    """Return (job_type, budget_text, budget_amount) from tile text.

    Hourly tiles put the whole rate range ("Hourly: $35.00 - $42.00")
    directly in the job-type-label text; fixed-price tiles put a
    separate "Est. budget: $X.XX" string. budget_amount takes the upper
    end of an hourly range so hourly and fixed jobs sort comparably.
    """
    job_type_text = job_type_text or ""
    if job_type_text.strip().lower().startswith("hourly"):
        amounts = [
            float(x.replace(",", ""))
            for x in re.findall(r"[\d,]+\.\d\d", job_type_text)
        ]
        amount = max(amounts) if amounts else None
        return "hourly", job_type_text.strip(), amount
    amounts = (
        [
            float(x.replace(",", ""))
            for x in re.findall(r"[\d,]+\.\d\d", fixed_price_text)
        ]
        if fixed_price_text
        else []
    )
    amount = amounts[0] if amounts else None
    return "fixed", (fixed_price_text or "").strip(), amount


def normalize_tile(raw, source_query):
    job_type, budget_text, budget_amount = parse_budget(
        raw.get("job_type_text", ""), raw.get("fixed_price_text", "")
    )
    path = raw.get("path") or ""
    url = path if path.startswith("http") else f"https://www.upwork.com{path}"
    return {
        "url": url,
        "title": (raw.get("title") or "").strip(),
        "job_type": job_type,
        "experience_level": raw.get("experience_level"),
        "budget_text": budget_text,
        "budget_amount": budget_amount,
        "posted_text": raw.get("posted_text"),
        "description": raw.get("description"),
        "skills": ",".join(raw.get("skills") or []),
        "source_query": source_query,
    }


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            url TEXT PRIMARY KEY,
            title TEXT,
            job_type TEXT,
            experience_level TEXT,
            budget_amount REAL,
            budget_text TEXT,
            posted_text TEXT,
            description TEXT,
            skills TEXT,
            source_query TEXT,
            scraped_at TEXT
        )
        """
    )
    return conn


def upsert_jobs(conn, jobs, scraped_at):
    for j in jobs:
        conn.execute(
            """
            INSERT INTO jobs (url, title, job_type, experience_level, budget_amount,
                               budget_text, posted_text, description, skills, source_query, scraped_at)
            VALUES (:url, :title, :job_type, :experience_level, :budget_amount,
                    :budget_text, :posted_text, :description, :skills, :source_query, :scraped_at)
            ON CONFLICT(url) DO UPDATE SET
                title=excluded.title, job_type=excluded.job_type,
                experience_level=excluded.experience_level, budget_amount=excluded.budget_amount,
                budget_text=excluded.budget_text, posted_text=excluded.posted_text,
                description=excluded.description, skills=excluded.skills,
                source_query=excluded.source_query, scraped_at=excluded.scraped_at
            """,
            {**j, "scraped_at": scraped_at},
        )
    conn.commit()


def print_cards(conn, min_budget, experience_level):
    query = "SELECT title, budget_text, posted_text, skills, url FROM jobs WHERE 1=1"
    sql_args = []
    if min_budget is not None:
        query += " AND budget_amount >= ?"
        sql_args.append(min_budget)
    if experience_level:
        query += " AND lower(experience_level) = ?"
        sql_args.append(experience_level.lower())
    query += " ORDER BY budget_amount DESC NULLS LAST"
    rows = conn.execute(query, sql_args).fetchall()
    for title, budget_text, posted_text, skills, url in rows:
        print("-" * 60)
        print(title)
        print(f"{budget_text}  |  {posted_text}")
        if skills:
            print(skills)
        print(url)
    print("-" * 60)
    print(f"{len(rows)} jobs", file=sys.stderr)


def load_scenario(name, scenarios_path):
    with open(scenarios_path) as f:
        scenarios = yaml.safe_load(f) or {}
    if name not in scenarios:
        raise SystemExit(
            f"unknown scenario {name!r}; available: {', '.join(scenarios)}"
        )
    return scenarios[name]


def main():
    ap = argparse.ArgumentParser(
        description="Upwork job-search URL builder + SQLite ingester."
    )
    ap.add_argument("--scenario", help="named search from scenarios.yaml")
    ap.add_argument("--scenarios-file", default=str(DEFAULT_SCENARIOS))
    ap.add_argument("--query", help="Upwork search query string; overrides scenario")
    ap.add_argument("--pages", type=int, default=3, help="page count for --print-urls")
    ap.add_argument("--location", help="client location filter; overrides scenario")
    ap.add_argument(
        "--min-budget", type=float, help="print-mode filter on budget_amount"
    )
    ap.add_argument("--experience-level", help="print-mode filter on experience_level")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--print", action="store_true", help="print cards from the DB")
    ap.add_argument(
        "--print-urls",
        action="store_true",
        help="print search URLs to navigate to, one per page",
    )
    ap.add_argument(
        "--ingest", help="JSON file of raw extracted tiles to normalize + upsert"
    )
    args = ap.parse_args()

    scenario = (
        load_scenario(args.scenario, args.scenarios_file) if args.scenario else {}
    )
    conn = init_db(args.db)

    if args.print:
        min_budget = (
            args.min_budget
            if args.min_budget is not None
            else scenario.get("min_budget")
        )
        experience_level = args.experience_level or scenario.get("experience_level")
        print_cards(conn, min_budget, experience_level)
        return

    params = dict(DEFAULT_PARAMS)
    params.update(scenario)
    if args.query:
        params["query"] = args.query
    if args.location:
        params["location"] = args.location
    if not params.get("query"):
        raise SystemExit("no query: pass --scenario or --query")

    if args.print_urls:
        for page_num in range(1, args.pages + 1):
            print(build_url(params, page_num))
        return

    if args.ingest:
        with open(args.ingest) as f:
            raw_tiles = json.load(f)
        jobs = [normalize_tile(t, params["query"]) for t in raw_tiles]
        upsert_jobs(conn, jobs, time.strftime("%Y-%m-%dT%H:%M:%S"))
        print(f"ingested {len(jobs)} jobs -> {args.db}", file=sys.stderr)
        return

    raise SystemExit("nothing to do: pass --print-urls, --ingest <file>, or --print")


if __name__ == "__main__":
    main()
