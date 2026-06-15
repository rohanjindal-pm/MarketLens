#!/usr/bin/env python3
"""
use_client.py - list clients or set the active client.

    python tools/use_client.py              # show active client + all clients
    python tools/use_client.py ivp          # make 'ivp' the active client
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="List clients or set the active client.")
    ap.add_argument("slug", nargs="?", help="Client slug to make active")
    args = ap.parse_args(argv)

    if args.slug:
        slug = common.slugify(args.slug)
        if slug not in common.list_clients():
            print(f"Note: no clients/{slug}/brand_kit.json yet — set active anyway.")
        common.set_active(slug)
        print(f"active_client = {slug}")
        return 0

    cfg = common.load_config()
    print(f"active_client: {cfg.get('active_client') or '(none)'}")
    clients = common.list_clients()
    print(f"clients ({len(clients)}): {', '.join(clients) or '(none)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
