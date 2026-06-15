#!/usr/bin/env python3
"""
save_snapshot.py - validate the agent-assembled competitor findings and persist a dated run.

The agent (following workflows/competitor_analysis.md) writes its research into a findings JSON
(default .tmp/findings.json), then calls this tool to validate + store it as the canonical
snapshot for the run. Snapshots are DURABLE (needed for change tracking) and live at:
    data/runs/<YYYY-MM-DD>/snapshot.json

Snapshot schema (v1):
{
  "schema_version": 1,
  "run_date": "YYYY-MM-DD",
  "business": { "name", "url", "tagline", "category", "value_prop", "target_customers", ... },
  "market_summary": {
     "overview": str,
     "key_trends": [str],
     "whats_working_for_competitors": [str],
     "your_opportunities": [ {"title", "rationale", "priority": "high|medium|low"} ]
  },
  "competitors": [ {
     "name", "url", "one_liner", "positioning", "target_customers",
     "products": [str], "key_features": [str], "pricing": str,
     "strengths": [str], "weaknesses": [str], "marketing_content": str,
     "social_presence": {..}, "customer_sentiment": str,
     "recent_moves": [str], "sources": [str]
  } ],
  "sources": [str],
  "methodology_note": str
}

CLI:
    python tools/save_snapshot.py .tmp/findings.json [--date 2026-06-15]

If "business" is omitted from the input, business_profile.json (if present) is used.
WAT `tools/` layer (deterministic).
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

COMPETITOR_DEFAULTS = {
    "url": None, "one_liner": "", "positioning": "", "target_customers": "",
    "products": [], "key_features": [], "pricing": "Not publicly listed",
    "strengths": [], "weaknesses": [], "marketing_content": "",
    "social_presence": {}, "customer_sentiment": "", "recent_moves": [], "sources": [],
}


def load_json(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_and_fill(data, run_date, profile_path="business_profile.json"):
    errors = []
    if not isinstance(data, dict):
        return None, ["top-level JSON must be an object"]

    business = data.get("business")
    if not business and profile_path and os.path.exists(profile_path):
        business = load_json(profile_path)
    business = business or {}
    if not business.get("name"):
        errors.append(f"business.name is required (provide it or create {profile_path})")

    comps = data.get("competitors") or []
    if not comps:
        errors.append("competitors[] must be a non-empty list")

    filled = []
    for i, c in enumerate(comps):
        if not isinstance(c, dict) or not c.get("name"):
            errors.append(f"competitors[{i}] must be an object with a 'name'")
            continue
        filled.append({**COMPETITOR_DEFAULTS, **c})

    if errors:
        return None, errors

    all_sources = sorted({s for c in filled for s in (c.get("sources") or []) if s})
    out = {
        "schema_version": 1,
        "run_date": run_date,
        "business": business,
        "market_summary": data.get("market_summary") or {},
        "competitors": filled,
        "sources": data.get("sources") or all_sources,
        "methodology_note": data.get("methodology_note")
        or f"Free web research (WebSearch/WebFetch) on {run_date}.",
    }
    return out, []


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate + persist a dated competitor snapshot.")
    ap.add_argument("input", help="Findings JSON file (or - for stdin)")
    ap.add_argument("--date", default=None, help="Run date YYYY-MM-DD (default: today)")
    common.add_client_arg(ap)
    args = ap.parse_args(argv)

    slug = common.resolve_slug(args.client)
    paths = common.client_paths(slug)
    run_date = args.date or datetime.date.today().isoformat()
    try:
        data = load_json(args.input)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR reading input: {exc}", file=sys.stderr)
        return 1

    snapshot, errors = validate_and_fill(data, run_date, paths["profile"])
    if errors:
        print("SCHEMA VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    dest_dir = os.path.join(paths["runs"], run_date)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "snapshot.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    print(f"Saved snapshot [{slug}]: {dest}")
    print(f"  business: {snapshot['business'].get('name')}")
    print(f"  competitors: {len(snapshot['competitors'])} "
          f"({', '.join(c['name'] for c in snapshot['competitors'])})")
    print(f"  opportunities: {len(snapshot['market_summary'].get('your_opportunities', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
