#!/usr/bin/env python3
"""
fetch_site.py - deterministic URL fetcher + HTML cleaner for the competitor-analysis workflow.

Fetches one or more URLs with `requests`, parses with BeautifulSoup (lxml), and extracts:
  title, meta description, visible text, same-domain links, stylesheet URLs,
  image references, icon/og:image references.

CLI:
    python tools/fetch_site.py https://www.ivp.in/ [more_urls ...] [--out .tmp]

Importable:
    from fetch_site import fetch, save_fetch
    data = fetch("https://www.ivp.in/")

Per-URL artifacts written to the output dir (default .tmp/):
    <slug>.html   raw HTML
    <slug>.txt    cleaned visible text
    <slug>.json   structured metadata (text truncated; html_path points to the .html)

Free / no API keys. Part of the WAT `tools/` layer (deterministic execution).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_OUT = ".tmp"
TIMEOUT = 20
MAX_TEXT_CHARS = 40000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def slugify(url: str) -> str:
    """Filesystem-safe slug derived from a URL's host + path."""
    p = urlparse(url)
    base = (p.netloc + p.path).strip("/")
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base) or "index"
    if len(base) > 60:
        base = base[:60] + "_" + hashlib.sha1(url.encode()).hexdigest()[:8]
    return base


def _clean_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "template", "head"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text(separator="\n").splitlines()]
    return "\n".join(ln for ln in lines if ln)[:MAX_TEXT_CHARS]


def fetch(url: str, timeout: int = TIMEOUT) -> dict:
    """Fetch a single URL -> structured dict. Never raises on network errors (sets ok=False)."""
    result = {
        "url": url, "final_url": None, "status": None, "ok": False,
        "title": None, "description": None, "text": "", "links": [],
        "stylesheets": [], "images": [], "icons": [], "og_image": None,
        "html": "", "error": None,
    }
    try:
        resp = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
        )
        result["final_url"] = resp.url
        result["status"] = resp.status_code
        resp.raise_for_status()
    except requests.RequestException as exc:
        result["error"] = str(exc)
        return result

    html = resp.text
    base = resp.url
    result["html"] = html
    soup = BeautifulSoup(html, "lxml")

    if soup.title and soup.title.string:
        result["title"] = soup.title.string.strip()
    md = (soup.find("meta", attrs={"name": "description"})
          or soup.find("meta", attrs={"property": "og:description"}))
    if md and md.get("content"):
        result["description"] = md["content"].strip()
    ogi = soup.find("meta", attrs={"property": "og:image"})
    if ogi and ogi.get("content"):
        result["og_image"] = urljoin(base, ogi["content"])

    domain = urlparse(base).netloc
    links = set()
    for a in soup.find_all("a", href=True):
        full = urljoin(base, a["href"]).split("#")[0]
        if full.startswith("http") and urlparse(full).netloc == domain:
            links.add(full)
    result["links"] = sorted(links)

    css = []
    for link in soup.find_all("link", href=True):
        rels = " ".join(r.lower() for r in (link.get("rel") or []))
        if "stylesheet" in rels:
            css.append(urljoin(base, link["href"]))
        if "icon" in rels:  # covers "icon" and "apple-touch-icon"
            result["icons"].append({"rel": rels, "href": urljoin(base, link["href"])})
    result["stylesheets"] = css

    result["images"] = [
        {"src": urljoin(base, img["src"]),
         "alt": (img.get("alt") or "").strip(),
         "class": " ".join(img.get("class") or [])}
        for img in soup.find_all("img", src=True)
    ]

    result["text"] = _clean_text(html)
    result["ok"] = True
    return result


def save_fetch(result: dict, outdir: str = DEFAULT_OUT) -> dict:
    """Persist a fetch() result to <outdir>/<slug>.{html,txt,json}. Returns the paths."""
    os.makedirs(outdir, exist_ok=True)
    slug = slugify(result["url"])
    html_path = os.path.join(outdir, f"{slug}.html")
    txt_path = os.path.join(outdir, f"{slug}.txt")
    json_path = os.path.join(outdir, f"{slug}.json")

    if result.get("html"):
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(result["html"])
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(result.get("text", ""))

    meta = {k: v for k, v in result.items() if k != "html"}
    meta["html_path"] = html_path if result.get("html") else None
    meta["text_path"] = txt_path
    meta["text"] = meta.get("text", "")[:4000]  # keep the json light
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    return {"slug": slug, "json": json_path, "html": html_path, "text": txt_path}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch + clean URL(s) for the competitor workflow.")
    ap.add_argument("urls", nargs="+", help="URL(s) to fetch")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output dir (default .tmp)")
    args = ap.parse_args(argv)

    summary = []
    for url in args.urls:
        res = fetch(url)
        saved = save_fetch(res, args.out)
        summary.append({
            "url": url, "ok": res["ok"], "status": res["status"], "title": res["title"],
            "n_links": len(res["links"]), "n_css": len(res["stylesheets"]),
            "n_images": len(res["images"]), "error": res["error"], **saved,
        })
        flag = "OK " if res["ok"] else "ERR"
        print(f"[{flag}] {url} -> {saved['json']}" + (f"  ({res['error']})" if res["error"] else ""))
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(s["ok"] for s in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
