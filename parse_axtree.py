#!/usr/bin/env python3
"""
Parse the read_page accessibility-tree text dump (saved by claude-in-chrome
to a tool-results JSON file when output is large) into the raw tile JSON
shape scrape.py --ingest expects.

Usage: python parse_axtree.py <tool-result.json> > tiles.json
"""

import json
import re
import sys


def clean_path(path):
    """Strip Upwork's search-term-highlight markup out of a job URL slug.

    When a query term matches a word in the title, Upwork wraps it as
    span-class-highlight-<word>-span- inside the slug itself (not just
    display HTML), e.g. ".../span-class-highlight-Remote-span-Event-..."
    instead of ".../Remote-Event-...". Strip the wrapper tokens, keep the
    highlighted word.
    """
    return path.replace("span-class-highlight-", "").replace("-span-", "-")


def parse(text):
    lines = text.split("\n")
    tiles = []
    cur = None
    state = None  # tracks which "list" section (job-type/exp/budget) we're in
    for line in lines:
        if re.match(r"^\s*article \[ref_\d+\]", line):
            if cur:
                tiles.append(cur)
            cur = {
                "title": None,
                "path": None,
                "job_type_text": None,
                "fixed_price_text": None,
                "experience_level": None,
                "posted_text": None,
                "skills": [],
            }
            state = None
            continue
        if cur is None:
            continue

        m = re.match(r'^\s*generic "(Posted[^"]*)" \[ref_\d+\]', line)
        if m and cur["posted_text"] is None:
            cur["posted_text"] = m.group(1)
            continue

        m = re.match(r'^\s*link "[^"]*" \[ref_\d+\] href="([^"]*)"', line)
        if m and cur["path"] is None:
            cur["path"] = clean_path(m.group(1).split("?")[0])
            continue
        m = re.match(r'^\s*heading "([^"]*)" \[ref_\d+\]', line)
        if m and cur["title"] is None:
            cur["title"] = m.group(1)
            continue

        m = re.match(r'^\s*generic "(Fixed price|Hourly[^"]*)" \[ref_\d+\]', line)
        if m and cur["job_type_text"] is None:
            cur["job_type_text"] = m.group(1)
            continue
        m = re.match(
            r'^\s*generic "(Entry Level|Intermediate|Expert)" \[ref_\d+\]', line
        )
        if m:
            cur["experience_level"] = m.group(1)
            continue
        m = re.match(r'^\s*generic "Est\. budget:" \[ref_\d+\]', line)
        if m:
            state = "budget"
            continue
        if state == "budget":
            m = re.match(r'^\s*generic "(\$[\d,.]+)" \[ref_\d+\]', line)
            if m:
                cur["fixed_price_text"] = f"Est. budget: {m.group(1)}"
            state = None
            continue

        m = re.match(r"^\s*button \[ref_\d+\]", line)
        if m:
            state = "skill"
            continue
        if state == "skill":
            m = re.match(r'^\s*generic "([^"]*)" \[ref_\d+\]', line)
            if m:
                cur["skills"].append(m.group(1))
            state = None
            continue

    if cur:
        tiles.append(cur)
    return tiles


def main():
    with open(sys.argv[1]) as f:
        raw = f.read()
    try:
        data = json.loads(raw)
        text = data[0]["text"] if isinstance(data, list) else data["text"]
    except (json.JSONDecodeError, KeyError, IndexError):
        # Small results come back inline (not saved to a tool-results JSON
        # file) -- treat the file as the raw accessibility-tree text.
        text = raw
    tiles = parse(text)
    json.dump(tiles, sys.stdout)


if __name__ == "__main__":
    main()
