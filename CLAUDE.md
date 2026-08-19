# upwork-scraper

Builds Upwork search URLs and ingests job tiles into local SQLite (`jobs.db`). Cloudflare blocks Playwright/CDP, so fetching is an agent in a real Chrome session (`claude-in-chrome`); `scrape.py` does not fetch.

## Stack
Python 3. `scenarios.yaml` holds search presets.

## Do not
- Commit `jobs.db` or `.venv/`.
- Try to cron a headless scrape. It will hang on Turnstile.
