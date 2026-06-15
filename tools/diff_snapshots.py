#!/usr/bin/env python3
"""
diff_snapshots.py - compare the two most recent run snapshots and emit a change report.

Powers the "What changed since last run" section. Compares the latest snapshot in data/runs/
against the previous one and writes data/runs/<latest>/changes.json:

{
  "baseline": bool,                # true when there's no prior run to compare
  "latest_date", "previous_date",
  "competitors_added": [name],
  "competitors_removed": [name],
  "changes": [ {"competitor", "field", "from", "to"} ],   # field-level deltas
  "new_recent_moves": [ {"competitor", "item"} ],
  "market_trends_added": [str], "market_trends_removed": [str],
  "summary": [str]                 # human-readable lines for the PDF
}

CLI:
    python tools/diff_snapshots.py [--runs-dir data/runs]

WAT `tools/` layer (deterministic).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

# Scalar fields worth tracking for change over time.
SCALAR_FIELDS = ["one_liner", "positioning", "pricing", "target_customers", "customer_sentiment"]
LIST_FIELDS = ["products", "key_features", "strengths", "weaknesses"]


def list_runs(runs_dir):
    if not os.path.isdir(runs_dir):
        return []
    runs = [d for d in os.listdir(runs_dir)
            if os.path.isfile(os.path.join(runs_dir, d, "snapshot.json"))]
    return sorted(runs)  # ISO date dir names sort chronologically


def load_snapshot(runs_dir, date):
    with open(os.path.join(runs_dir, date, "snapshot.json"), encoding="utf-8") as f:
        return json.load(f)


def by_name(snapshot):
    return {c["name"].strip().lower(): c for c in snapshot.get("competitors", [])}


def norm_set(items):
    return {str(x).strip().lower(): str(x).strip() for x in (items or [])}


def diff(latest, previous):
    out = {
        "baseline": False,
        "latest_date": latest["run_date"],
        "previous_date": previous["run_date"],
        "competitors_added": [], "competitors_removed": [],
        "changes": [], "new_recent_moves": [],
        "market_trends_added": [], "market_trends_removed": [],
        "summary": [],
    }
    cur, prev = by_name(latest), by_name(previous)

    added = [cur[k]["name"] for k in cur if k not in prev]
    removed = [prev[k]["name"] for k in prev if k not in cur]
    out["competitors_added"], out["competitors_removed"] = sorted(added), sorted(removed)

    for key in sorted(set(cur) & set(prev)):
        c_new, c_old = cur[key], prev[key]
        name = c_new["name"]
        for field in SCALAR_FIELDS:
            a, b = (c_old.get(field) or "").strip(), (c_new.get(field) or "").strip()
            if a != b and (a or b):
                out["changes"].append({"competitor": name, "field": field, "from": a, "to": b})
        for field in LIST_FIELDS:
            old_s, new_s = norm_set(c_old.get(field)), norm_set(c_new.get(field))
            gained = [new_s[k] for k in new_s if k not in old_s]
            lost = [old_s[k] for k in old_s if k not in new_s]
            if gained:
                out["changes"].append({"competitor": name, "field": f"{field}+", "from": "", "to": ", ".join(gained)})
            if lost:
                out["changes"].append({"competitor": name, "field": f"{field}-", "from": ", ".join(lost), "to": ""})
        old_moves, new_moves = norm_set(c_old.get("recent_moves")), norm_set(c_new.get("recent_moves"))
        for k in new_moves:
            if k not in old_moves:
                out["new_recent_moves"].append({"competitor": name, "item": new_moves[k]})

    t_old = norm_set(previous.get("market_summary", {}).get("key_trends"))
    t_new = norm_set(latest.get("market_summary", {}).get("key_trends"))
    out["market_trends_added"] = [t_new[k] for k in t_new if k not in t_old]
    out["market_trends_removed"] = [t_old[k] for k in t_old if k not in t_new]

    out["summary"] = build_summary(out)
    return out


def build_summary(out):
    s = []
    if out["competitors_added"]:
        s.append(f"New competitors tracked: {', '.join(out['competitors_added'])}.")
    if out["competitors_removed"]:
        s.append(f"No longer tracked: {', '.join(out['competitors_removed'])}.")
    for ch in out["changes"]:
        fld = ch["field"]
        if fld.endswith("+"):
            s.append(f"{ch['competitor']} added {fld[:-1]}: {ch['to']}.")
        elif fld.endswith("-"):
            s.append(f"{ch['competitor']} dropped {fld[:-1]}: {ch['from']}.")
        else:
            s.append(f"{ch['competitor']} {fld} changed: “{ch['from']}” -> “{ch['to']}”.")
    for mv in out["new_recent_moves"]:
        s.append(f"{mv['competitor']}: {mv['item']}")
    for t in out["market_trends_added"]:
        s.append(f"New market trend: {t}.")
    if not s:
        s.append("No material changes detected since the previous run.")
    return s


def main(argv=None):
    ap = argparse.ArgumentParser(description="Diff the two most recent competitor snapshots.")
    common.add_client_arg(ap)
    args = ap.parse_args(argv)

    runs_dir = common.client_paths(common.resolve_slug(args.client))["runs"]
    runs = list_runs(runs_dir)
    if not runs:
        print("No snapshots found.", file=sys.stderr)
        return 1

    latest_date = runs[-1]
    latest = load_snapshot(runs_dir, latest_date)
    if len(runs) == 1:
        changes = {"baseline": True, "latest_date": latest_date, "previous_date": None,
                   "competitors_added": [c["name"] for c in latest.get("competitors", [])],
                   "competitors_removed": [], "changes": [], "new_recent_moves": [],
                   "market_trends_added": [], "market_trends_removed": [],
                   "summary": ["Baseline run — first snapshot, nothing to compare against yet."]}
    else:
        changes = diff(latest, load_snapshot(runs_dir, runs[-2]))

    dest = os.path.join(runs_dir, latest_date, "changes.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(changes, f, indent=2, ensure_ascii=False)

    print(f"Wrote {dest} (baseline={changes['baseline']})")
    for line in changes["summary"]:
        print(f"  - {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
