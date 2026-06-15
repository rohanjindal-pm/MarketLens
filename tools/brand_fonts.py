#!/usr/bin/env python3
"""
brand_fonts.py - give a client's report the brand's REAL typography.

Most corporate sites use Google Fonts (e.g. Inter, Manrope, Poppins). Google's CSS API serves
TrueType when requested with an old User-Agent, so we can fetch the actual brand font as .ttf and
embed it in the PDF. If the brand font can't be fetched, we install DejaVu Sans (ships with
matplotlib; open-licensed and cross-platform) so the report still renders identically anywhere.

CLI (updates clients/<slug>/brand_kit.json fonts, preserving colors/logo):
    python tools/brand_fonts.py --client ivp                 # use the font detected on the site
    python tools/brand_fonts.py --client acme --family Poppins
    python tools/brand_fonts.py --client acme --no-google     # portable fallback only

Importable: ensure_fonts(family, dest_dir) / apply_to_brand_kit(slug, family).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

OLD_UA = "Mozilla/4.0"  # makes Google Fonts serve .ttf instead of .woff2
VARIANTS = {("normal", "400"): "regular", ("normal", "700"): "bold",
            ("italic", "400"): "italic", ("italic", "700"): "bolditalic"}


def download_google_font(family: str, dest_dir: str):
    """Fetch regular/bold/italic/bolditalic .ttf for a Google font family. None if unavailable."""
    api = f"https://fonts.googleapis.com/css?family={family.replace(' ', '+')}:400,700,400italic,700italic"
    try:
        r = requests.get(api, headers={"User-Agent": OLD_UA}, timeout=20)
    except requests.RequestException:
        return None
    if not r.ok or "@font-face" not in r.text:
        return None
    os.makedirs(dest_dir, exist_ok=True)
    out = {}
    for block in re.findall(r"@font-face\s*{([^}]*)}", r.text):
        style = "italic" if "italic" in block else "normal"
        weight = "700" if re.search(r"font-weight:\s*700", block) else "400"
        key = VARIANTS.get((style, weight))
        m = re.search(r"url\((https://[^)]+\.ttf)\)", block)
        if not key or not m:
            continue
        try:
            tt = requests.get(m.group(1), headers={"User-Agent": OLD_UA}, timeout=20)
        except requests.RequestException:
            continue
        if tt.ok and len(tt.content) > 1000:
            path = os.path.join(dest_dir, f"{family.replace(' ', '')}-{key}.ttf")
            with open(path, "wb") as f:
                f.write(tt.content)
            out[key] = path
    return out or None


def install_fallback(dest_dir: str):
    """Copy DejaVu Sans (bundled with matplotlib; portable + open-licensed) into dest_dir."""
    import matplotlib
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    mapping = {"regular": "DejaVuSans.ttf", "bold": "DejaVuSans-Bold.ttf",
               "italic": "DejaVuSans-Oblique.ttf", "bolditalic": "DejaVuSans-BoldOblique.ttf"}
    os.makedirs(dest_dir, exist_ok=True)
    out = {}
    for key, fn in mapping.items():
        src = os.path.join(base, fn)
        if os.path.exists(src):
            dst = os.path.join(dest_dir, fn)
            shutil.copyfile(src, dst)
            out[key] = dst
    return out


def _complete(fonts: dict) -> dict:
    reg = fonts.get("regular") or next(iter(fonts.values()), None)
    for k in ("regular", "bold", "italic", "bolditalic"):
        if not fonts.get(k):
            fonts[k] = reg
    return fonts


def _family_candidates(family: str) -> list:
    """Try the detected family, then a cleaned name (e.g. 'Inter Variable' -> 'Inter')."""
    cands = [family]
    cleaned = re.sub(r"\s*\(.*?\)", "", family)
    cleaned = re.sub(r"\s*(variable|vf|var)\b", "", cleaned, flags=re.I).strip()
    if cleaned and cleaned.lower() != family.lower():
        cands.append(cleaned)
    return cands


def ensure_fonts(family: str | None, dest_dir: str, allow_google: bool = True) -> dict:
    """Install the brand font (Google -> TTF) or DejaVu fallback. Returns a fonts dict."""
    fonts, source = None, None
    if family and allow_google:
        for cand in _family_candidates(family):
            fonts = download_google_font(cand, dest_dir)
            if fonts:
                source, family = f"google:{cand}", cand
                break
    if not fonts:
        fonts = install_fallback(dest_dir)
        family, source = "DejaVu Sans", "fallback:DejaVu Sans"
    fonts = _complete(fonts)
    return {"family_name": family, "source": source,
            "regular": fonts["regular"], "bold": fonts["bold"],
            "italic": fonts["italic"], "bolditalic": fonts["bolditalic"]}


def apply_to_brand_kit(slug: str, family: str | None = None, allow_google: bool = True) -> dict:
    """Install fonts for a client and update brand_kit.json fonts (colors/logo untouched)."""
    p = common.client_paths(slug)
    if not os.path.exists(p["brand_kit"]):
        raise SystemExit(f"No brand_kit at {p['brand_kit']}")
    with open(p["brand_kit"], encoding="utf-8") as f:
        bk = json.load(f)
    if not family:
        detected = (bk.get("fonts") or {}).get("detected_on_site") or []
        family = detected[0] if detected else None
    fonts = ensure_fonts(family, p["fonts"], allow_google)
    fonts["detected_on_site"] = (bk.get("fonts") or {}).get("detected_on_site", [])
    bk["fonts"] = fonts
    with open(p["brand_kit"], "w", encoding="utf-8") as f:
        json.dump(bk, f, indent=2, ensure_ascii=False)
    return fonts


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install a brand font for a client (Google Fonts -> TTF, else DejaVu).")
    common.add_client_arg(ap)
    ap.add_argument("--family", default=None, help="Font family (default: detected_on_site in brand_kit)")
    ap.add_argument("--no-google", action="store_true", help="Skip Google Fonts; use portable fallback")
    args = ap.parse_args(argv)
    slug = common.resolve_slug(args.client)
    fonts = apply_to_brand_kit(slug, args.family, allow_google=not args.no_google)
    print(f"[{slug}] brand font -> {fonts['family_name']}  ({fonts['source']})")
    for k in ("regular", "bold", "italic", "bolditalic"):
        print(f"   {k}: {fonts[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
