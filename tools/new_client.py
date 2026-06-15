#!/usr/bin/env python3
"""
new_client.py - one-command scaffold for a new competitor-analysis client.

Does the deterministic setup in a single step:
  1. Branding   : runs extract_brand (palette + logo + downloads the brand's REAL font) -> clients/<slug>/.
  2. Profile material : fetches the homepage + key internal pages (about/product/pricing) -> .tmp/<slug>_site.txt.
  3. Profile stub : writes clients/<slug>/business_profile.json (name/url/description prefilled; rest blank).
  4. Sets the client active.

After this, the agent drafts the business profile from the fetched text and the user confirms branding;
then competitor research proceeds. (Writing the profile narrative is an LLM step, not a deterministic one,
so this tool prepares the material and a stub rather than inventing the content.)

    python tools/new_client.py https://acme.com [--slug acme] [--name "Acme Corp"] [--no-google]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import extract_brand  # noqa: E402
from fetch_site import fetch  # noqa: E402

KEY_PAGE_RE = re.compile(
    r"/(about|company|who-we-are|product|products|platform|solution|solutions|pricing|services)(/|$)", re.I)


def gather_profile_text(homepage, base, outpath, max_pages=4):
    """Save homepage + a few key internal pages' visible text for the agent to draft the profile from."""
    chunks = [f"# {homepage.get('title', '')}\nURL: {base}\n\n{homepage.get('text', '')}"]
    seen, picked = {base.rstrip("/")}, []
    for link in homepage.get("links", []):
        if KEY_PAGE_RE.search(urlparse(link).path) and link.rstrip("/") not in seen:
            picked.append(link)
            seen.add(link.rstrip("/"))
        if len(picked) >= max_pages - 1:
            break
    for link in picked:
        r = fetch(link)
        if r["ok"]:
            chunks.append(f"\n\n===== {link} =====\n{r.get('text', '')[:6000]}")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("\n".join(chunks))
    return [base] + picked


def write_profile_stub(slug, name, base, description):
    p = common.client_paths(slug)["profile"]
    if os.path.exists(p):
        return p, False
    stub = {
        "name": name, "url": base, "tagline": "", "category": "",
        "description": description or "", "value_prop": "", "target_customers": "",
        "differentiators": [],
        "_status": f"DRAFT - fill in from .tmp/{slug}_site.txt, then confirm with the user.",
    }
    with open(p, "w", encoding="utf-8") as f:
        json.dump(stub, f, indent=2, ensure_ascii=False)
    return p, True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Scaffold a new client in one step: branding + brand font + profile material.")
    ap.add_argument("url")
    ap.add_argument("--slug", default=None, help="Client slug (default: derived from company name)")
    ap.add_argument("--name", default=None, help="Company name (default: from <title>)")
    ap.add_argument("--no-google", action="store_true", help="Skip Google brand-font download (portable fallback)")
    args = ap.parse_args(argv)

    print(f"[1/4] Fetching {args.url} ...")
    home = fetch(args.url)
    if not home["ok"]:
        print(f"ERROR: could not fetch site: {home['error']}", file=sys.stderr)
        return 1
    base = home["final_url"] or args.url
    name = args.name or extract_brand.derive_name(home.get("title"), base)
    slug = common.slugify(args.slug or name)
    if os.path.exists(common.client_paths(slug)["brand_kit"]):
        print(f"  note: client '{slug}' already exists — branding will be refreshed (re-confirm colors).")
    common.ensure_dirs(slug)

    print(f"[2/4] Branding for client '{slug}' ({name}) ...")
    brand_argv = [args.url, "--client", slug, "--name", name] + (["--no-google"] if args.no_google else [])
    rc = extract_brand.main(brand_argv)
    if rc != 0:
        return rc

    print(f"[3/4] Gathering profile material -> .tmp/{slug}_site.txt ...")
    sitetext = os.path.join(".tmp", f"{slug}_site.txt")
    pages = gather_profile_text(home, base, sitetext)

    print("[4/4] Writing business_profile.json stub ...")
    ppath, created = write_profile_stub(slug, name, base, home.get("description"))
    common.set_active(slug)

    p = common.client_paths(slug)
    print("\n" + "=" * 64)
    print(f"Client '{slug}' scaffolded and set active.")
    print(f"  brand kit : {p['brand_kit']}   (review {p['brand']}/palette_preview.png + logo candidates)")
    print(f"  profile   : {ppath}   ({'stub created' if created else 'already existed - kept'})")
    print(f"  material  : {sitetext}   ({len(pages)} page(s) of site text)")
    print("\nNext steps:")
    print(f"  1. Confirm branding (colors/logo/font) in {p['brand_kit']}.")
    print(f"  2. Draft {ppath} from {sitetext}, then confirm with the user.")
    print("  3. Discover + research competitors -> .tmp/findings.json")
    print(f"  4. save_snapshot / diff_snapshots / generate_report  --client {slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
