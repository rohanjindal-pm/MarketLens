#!/usr/bin/env python3
"""
common.py - shared helpers for the multi-client competitor-analysis workflow.

Each target company ("client") gets an isolated folder so analyses never collide:

    clients/<slug>/
        business_profile.json      # who the client is
        brand_kit.json             # palette, fonts, logo (the report's look & feel)
        brand/                      # logo images
        brand/fonts/                # the brand's real .ttf fonts
        data/runs/<date>/           # durable snapshots + diffs (per client)
        reports/                    # generated PDFs

The "active" client is stored in config.json ({"active_client": "ivp"}). Every tool accepts
--client <slug> to override it. Tools are run from the project root: `uv run python tools/<t>.py`.
"""
from __future__ import annotations

import json
import os
import re

CONFIG_PATH = "config.json"
CLIENTS_DIR = "clients"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "client"


def load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def set_active(slug: str) -> None:
    cfg = load_config()
    cfg["active_client"] = slug
    save_config(cfg)


def resolve_slug(arg_slug: str | None) -> str:
    """Resolve the client slug from --client, else the active client in config.json."""
    if arg_slug:
        return slugify(arg_slug)
    cfg = load_config()
    if cfg.get("active_client"):
        return cfg["active_client"]
    raise SystemExit(
        "No client selected. Pass --client <slug>, or set one with "
        "`uv run python tools/use_client.py <slug>`."
    )


def client_paths(slug: str) -> dict:
    d = os.path.join(CLIENTS_DIR, slug)
    return {
        "slug": slug,
        "dir": d,
        "brand_kit": os.path.join(d, "brand_kit.json"),
        "profile": os.path.join(d, "business_profile.json"),
        "brand": os.path.join(d, "brand"),
        "fonts": os.path.join(d, "brand", "fonts"),
        "runs": os.path.join(d, "data", "runs"),
        "reports": os.path.join(d, "reports"),
    }


def ensure_dirs(slug: str) -> dict:
    p = client_paths(slug)
    for key in ("dir", "brand", "fonts", "runs", "reports"):
        os.makedirs(p[key], exist_ok=True)
    return p


def list_clients() -> list:
    if not os.path.isdir(CLIENTS_DIR):
        return []
    return sorted(d for d in os.listdir(CLIENTS_DIR)
                  if os.path.isfile(os.path.join(CLIENTS_DIR, d, "brand_kit.json")))


def add_client_arg(ap) -> None:
    ap.add_argument("--client", default=None,
                    help="Client slug (default: active_client from config.json)")
