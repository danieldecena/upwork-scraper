# upwork-scraper

Pulls Upwork job-search results into a local SQLite DB, and prints them back
as terminal "cards" filtered/sorted by budget.

## Why this isn't a script you just run

Upwork fronts job search with a Cloudflare Turnstile challenge that
fingerprints CDP-based automation. Confirmed dead ends: headless Playwright,
headed Playwright, headed Playwright on the real installed Chrome binary
(`channel="chrome"`) — all hang on "Just a moment..." indefinitely (tested
past 30s). Only a real, already-authenticated browser session (e.g. the
claude-in-chrome extension driving your actual Chrome profile) gets through,
in about 4 seconds.

So the pipeline is: an agent (Claude, driving claude-in-chrome) does the
fetching interactively; `scrape.py` only builds URLs and does the SQLite
side. There's no cron-able one-shot command — running a fresh scrape means
asking Claude to do it.

## Workflow (what the agent does per scrape)

1. `python scrape.py --print-urls --scenario <name> --pages N` — get the
   search URL for each page.
2. Navigate to each URL via claude-in-chrome, wait ~4s for the Cloudflare
   challenge to clear.
3. Extract job tiles. `javascript_tool`'s output has a small display cap
   (a handful of tiles' worth of JSON before it truncates) and also blocks
   output containing URL-query-string-like text — job descriptions
   sometimes embed links with query strings, which trips it even after
   truncating description text. Skip descriptions and use `read_page`
   instead (much larger `max_chars`, and large results get persisted to a
   `tool-results/*.json` file on disk instead of being truncated in-chat):

   ```
   find "job search results list container" -> get its ref_id
   read_page(ref_id=<that ref>, filter="all", max_chars=80000)
   ```

4. If the result was saved to a file (large output), parse it with
   `parse_axtree.py <tool-result.json> > tiles.json` — this regex-parses
   the accessibility-tree text dump into the tile shape `--ingest` expects,
   without pulling the raw dump into the agent's own context.
5. `python scrape.py --ingest tiles.json --scenario <name>` — normalizes
   (budget parsing, hourly-vs-fixed) and upserts into `jobs.db`, keyed on
   job URL so re-scraping the same listing just updates it.
6. Repeat 2-5 per page (small random delay between page loads — politeness
   against Forter's fingerprinting, not a workaround for anything that
   blocks the plain case).

## Selectors (data-test values are space-separated tokens — use `~=`, not `=`)

- `article.job-tile` — one per job card
- `[data-test~="job-tile-title-link"]` — title text + `href` (job URL)
- `[data-test~="job-type-label"]` — "Fixed price" or the full hourly range
  text ("Hourly: $35.00 - $42.00" — the range lives here, there's no
  separate hourly-rate selector)
- `[data-test~="is-fixed-price"]` — "Est. budget: $X.XX" (fixed-price only)
- `[data-test~="experience-level"]` — Entry Level / Intermediate / Expert
- `[data-test~="job-pubilshed-date"]` — posted-time text (Upwork's own
  typo, "pubilshed" — must match exactly)
- `[data-test~="JobAttrs"] [data-test~="token"]` — skill tags (`token` is
  a data-test value here too, not a CSS class)

## Filters confirmed as URL params

- `location=United+States` — client location (from the sidebar filter)
- `page=N` — pagination (client-side nav, doesn't re-trigger Cloudflare
  once the first page has cleared it)
- `q=...` — Upwork's own boolean query syntax, confirmed by driving the
  Advanced Search modal: `word AND (a OR b) AND NOT c AND "exact phrase"
  AND title:word`. The Skills Search box didn't compile into `q` from
  plain typed text — needs an autocomplete selection, not scripted here.

## Usage

```
source .venv/bin/activate
python scrape.py --print-urls --scenario quick-easy --pages 3
python scrape.py --ingest tiles.json --scenario quick-easy
python scrape.py --print --scenario high-paying
python scrape.py --print --min-budget 500 --experience-level expert
```

Scenarios live in `scenarios.yaml`.
