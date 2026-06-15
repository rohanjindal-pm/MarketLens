#!/usr/bin/env python3
"""
extract_brand.py - infer a brand kit (logo, color palette, fonts) from a company website.

Pipeline:
  1. Fetch the homepage (via fetch_site.fetch) -> HTML, icons, images, stylesheets.
  2. Logo candidates: decode Next.js /_next/image wrappers, score images that look like the
     company's OWN mark (brand tokens in PATH + "logo"), down-rank CMS/client-wall images
     (/wp-content/uploads/, *.wpengine.com), prioritize favicons/apple-touch-icon; download top few.
  3. Colors: download linked stylesheets + inline styles, regex hex/rgb + CSS vars; sample the
     brand icon + logo rasters (Pillow/numpy); rank by vividness-weighted frequency; propose roles.
  4. Fonts: scan CSS for font-family / @font-face / Google Fonts; record detected families.
     Embedding falls back to bundled Work Sans (clean professional sans) for reliable rendering.
  5. Write brand_kit.json (best-guess; user-confirmed later) + palette_preview.png.

CLI:
    python tools/extract_brand.py https://www.ivp.in/ [--name "Indus Valley Partners"]

The agent presents the result to the user for confirmation/editing before locking brand_kit.json.
Free / no API keys. WAT `tools/` layer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand_fonts  # noqa: E402
import common  # noqa: E402
from fetch_site import USER_AGENT, fetch, save_fetch  # noqa: E402


def _preview_font(size):
    """Portable TTF for palette-preview labels (DejaVu via matplotlib), else default."""
    try:
        import matplotlib
        return ImageFont.truetype(
            os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans.ttf"), size)
    except Exception:
        return ImageFont.load_default()
GENERIC_FAMILIES = {"sans-serif", "serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif",
                    "-apple-system", "blinkmacsystemfont", "inherit", "initial", "cursive", "fantasy",
                    "arial", "helvetica", "roboto"}


# ---------- color helpers ----------
def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r, g, b):
    return "#%02x%02x%02x" % (int(r), int(g), int(b))


def saturation(rgb):
    mx, mn = max(rgb) / 255.0, min(rgb) / 255.0
    return 0.0 if mx == 0 else (mx - mn) / mx


def is_neutral(rgb):
    mx, mn = max(rgb), min(rgb)
    if mx >= 236 and mn >= 230:   # near white
        return True
    if mx <= 30:                  # near black
        return True
    if (mx - mn) <= 14:           # gray
        return True
    return False


def luminance(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


# ---------- logo ----------
def decode_next_image(u, base):
    pr = urlparse(u)
    if "/_next/image" in pr.path:
        qs = parse_qs(pr.query)
        if "url" in qs:
            return urljoin(base, unquote(qs["url"][0]))
    return u


def is_cms_asset(url):
    pr = urlparse(url)
    return ("wpengine" in pr.netloc or "/wp-content/uploads/" in pr.path
            or pr.netloc.startswith("cms.") or "/cms/" in pr.path)


def brand_tokens(name, base):
    toks = set()
    host = urlparse(base).netloc.replace("www.", "").split(".")[0]
    if host:
        toks.add(host.lower())
    for w in re.split(r"\W+", name or ""):
        if len(w) >= 3 and w.lower() not in {"partners", "inc", "ltd", "the", "llc", "group", "company"}:
            toks.add(w.lower())
    return sorted(toks)


def score_logo(url, alt, cls, tokens):
    # Match tokens against the PATH only (not host) so a CMS host like "ivpcms" doesn't match "ivp".
    path_blob = f"{urlparse(url).path} {alt} {cls}".lower()
    s = 0
    if any(t in path_blob for t in tokens):
        s += 6
    if "logo" in path_blob:
        s += 3
    if url.lower().rsplit(".", 1)[-1] in ("svg", "png"):
        s += 1
    if any(k in path_blob for k in ("footer", "header", "nav")):
        s += 1
    if url.startswith("data:"):
        s -= 10
    if is_cms_asset(url):   # client-logo wall / CMS content, not the company's own mark
        s -= 9
    return s


def gather_logo_candidates(data, base, tokens):
    seen, cands = set(), []
    for img in data.get("images", []):
        url = decode_next_image(img["src"], base)
        if url in seen or url.startswith("data:"):
            continue
        seen.add(url)
        cands.append({"url": url, "alt": img.get("alt", ""), "cls": img.get("class", ""),
                      "score": score_logo(url, img.get("alt", ""), img.get("class", ""), tokens), "kind": "img"})
    for icon in data.get("icons", []):       # favicons / app icons: definitive brand marks
        url, rel = icon["href"], icon.get("rel", "")
        if url in seen or url.endswith(".ico") or "mask-icon" in rel:
            continue
        seen.add(url)
        cands.append({"url": url, "alt": "", "cls": rel, "kind": "icon",
                      "score": 5 if "apple-touch" in rel else 4})
    cands.sort(key=lambda c: c["score"], reverse=True)
    return cands


def download(url, dest):
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        if len(r.content) < 100:
            return False
        with open(dest, "wb") as f:
            f.write(r.content)
        return True
    except (requests.RequestException, OSError):
        return False


# ---------- css / colors / fonts ----------
def collect_css(data):
    chunks = []
    for href in data.get("stylesheets", []):
        try:
            r = requests.get(href, timeout=20, headers={"User-Agent": USER_AGENT})
            if r.ok:
                chunks.append(r.text)
        except requests.RequestException:
            pass
    html = ""
    if data.get("html_path") and os.path.exists(data["html_path"]):
        with open(data["html_path"], encoding="utf-8") as f:
            html = f.read()
    chunks.append(" ".join(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)))
    chunks.append(" ".join(re.findall(r'style="([^"]*)"', html)))
    return "\n".join(chunks)


def sample_image_colors(paths, top=6):
    """Dominant saturated colors across one or more raster images (alpha-aware), via numpy."""
    counter = Counter()
    for p in paths:
        if not p or p.lower().endswith(".svg") or not os.path.exists(p):
            continue
        try:
            im = Image.open(p).convert("RGBA")
        except (OSError, ValueError):
            continue
        im.thumbnail((160, 160))
        arr = np.asarray(im)
        if arr.ndim != 3 or arr.shape[2] < 4:
            continue
        rgb = arr[..., :3].reshape(-1, 3).astype(int)
        alpha = arr[..., 3].reshape(-1)
        mx, mn = rgb.max(1), rgb.min(1)
        keep = (alpha >= 200) & (mx - mn > 18) & (mx > 40) & ~((mx >= 236) & (mn >= 230))
        for r, g, b in (rgb[keep] // 16 * 16):
            counter[(int(r), int(g), int(b))] += 1
    return [rgb_to_hex(*c) for c, _ in counter.most_common(top)]


def extract_colors(css, image_paths):
    counter = Counter()
    for h in re.findall(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", css):
        counter[rgb_to_hex(*hex_to_rgb(h))] += 1
    for r, g, b in re.findall(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", css):
        rgb = (int(r), int(g), int(b))
        if all(0 <= v <= 255 for v in rgb):
            counter[rgb_to_hex(*rgb)] += 1

    logo_colors = set(sample_image_colors(image_paths))
    for hexc in logo_colors:
        counter[hexc] += 10  # brand-asset colors are strong evidence

    ranked = []
    for hexc, freq in counter.most_common():
        rgb = hex_to_rgb(hexc)
        ranked.append({"hex": hexc, "freq": freq, "sat": round(saturation(rgb), 3),
                       "lum": round(luminance(rgb), 1), "neutral": is_neutral(rgb),
                       "from_logo": hexc in logo_colors})
    return ranked


def extract_fonts(css):
    fams = Counter()
    for decl in re.findall(r"font-family\s*:\s*([^;}{]+)", css, re.I):
        first = decl.split(",")[0].strip().strip('"\'')
        if first and first.lower() not in GENERIC_FAMILIES and not first.startswith("var("):
            fams[first] += 1
    faces = []
    for block in re.findall(r"@font-face\s*{([^}]*)}", css, re.I):
        fam = re.search(r"font-family\s*:\s*([^;]+)", block, re.I)
        src = re.findall(r"url\(([^)]+)\)", block)
        faces.append({"family": fam.group(1).strip().strip('"\'') if fam else None,
                      "src": [s.strip('"\' ') for s in src][:3]})
    return {"families_ranked": [f for f, _ in fams.most_common(6)], "font_faces": faces[:8],
            "uses_google_fonts": bool(re.search(r"fonts\.(googleapis|gstatic)\.com", css))}


def propose_palette(ranked):
    brand = [c for c in ranked if not c["neutral"]]
    neutrals = [c for c in ranked if c["neutral"]]

    def vivid_key(c):  # vividness-weighted frequency; brand-asset colors get a 2x boost
        return c["freq"] * (0.4 + c["sat"]) * (2.0 if c["from_logo"] else 1.0)

    brand_sorted = sorted(brand, key=vivid_key, reverse=True)
    pal = {}
    if brand_sorted:
        pal["primary"] = brand_sorted[0]["hex"]
        p_rgb = hex_to_rgb(pal["primary"])
        sec = next((c["hex"] for c in brand_sorted[1:]
                    if abs(luminance(hex_to_rgb(c["hex"])) - luminance(p_rgb)) > 25
                    or abs(c["sat"] - saturation(p_rgb)) > 0.2), None)
        pal["secondary"] = sec or (brand_sorted[1]["hex"] if len(brand_sorted) > 1 else pal["primary"])
        pal["accent"] = max(brand, key=lambda c: c["sat"])["hex"]
    else:
        pal.update(primary="#0b2e4f", secondary="#1f6f8b", accent="#e0a13a")
    darks = sorted([c for c in neutrals if c["lum"] < 90], key=lambda c: c["lum"])
    lights = sorted([c for c in neutrals if c["lum"] > 200], key=lambda c: -c["lum"])
    pal["dark"] = darks[0]["hex"] if darks else "#1a1f29"
    pal["light"] = lights[0]["hex"] if lights else "#f6f7f9"
    pal["palette"] = [c["hex"] for c in brand_sorted[:6]]
    return pal


def make_preview(palette, path):
    roles = [("primary", palette["primary"]), ("secondary", palette["secondary"]),
             ("accent", palette["accent"]), ("dark", palette["dark"]), ("light", palette["light"])]
    w, h, pad = 200, 150, 10
    img = Image.new("RGB", (w * len(roles), h + 40), "#ffffff")
    draw = ImageDraw.Draw(img)
    font = _preview_font(18)
    for i, (role, hexc) in enumerate(roles):
        x0 = i * w
        draw.rectangle([x0 + pad, pad, x0 + w - pad, h], fill=hexc, outline="#cccccc")
        draw.text((x0 + pad, h + 8), role, fill="#222222", font=font)
        draw.text((x0 + pad, h + 22), hexc, fill="#666666", font=font)
    img.save(path)


def derive_name(title, base):
    if title:
        t = re.sub(r"^\s*(home|homepage|welcome)\s*[\-|–:]\s*", "", title, flags=re.I)
        t = re.split(r"\s*[\-|–]\s*", t)[0].strip()
        if t:
            return t
    return urlparse(base).netloc.replace("www.", "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Infer a brand kit (logo, palette, fonts) from a website.")
    ap.add_argument("url")
    ap.add_argument("--name", default=None, help="Company name (else derived from <title>)")
    common.add_client_arg(ap)
    ap.add_argument("--no-google", action="store_true",
                    help="Skip downloading the brand's Google font; use a portable fallback")
    args = ap.parse_args(argv)

    print(f"Fetching {args.url} ...")
    data = fetch(args.url)
    if not data["ok"]:
        print(f"ERROR: could not fetch site: {data['error']}", file=sys.stderr)
        return 1
    data["html_path"] = save_fetch(data, ".tmp")["html"]

    base = data["final_url"] or args.url
    name = args.name or derive_name(data.get("title"), base)
    slug = common.slugify(args.client) if args.client else common.slugify(name)
    paths = common.ensure_dirs(slug)
    assets_dir, out_path = paths["brand"], paths["brand_kit"]
    tokens = brand_tokens(name, base)
    print(f"Client: {slug}   Company: {name}   brand tokens: {tokens}")

    # logos + icon mark
    cands = gather_logo_candidates(data, base, tokens)
    downloaded, icon_path = [], None
    for i, c in enumerate(cands[:5]):
        ext = os.path.splitext(urlparse(c["url"]).path)[1].lower() or ".png"
        if ext not in (".png", ".svg", ".jpg", ".jpeg", ".webp"):
            ext = ".png"
        dest = os.path.join(assets_dir, f"logo_candidate_{i + 1}{ext}")
        if download(c["url"], dest):
            downloaded.append({"path": dest, "url": c["url"], "alt": c["alt"],
                               "score": c["score"], "kind": c["kind"]})
            if c["kind"] == "icon" and icon_path is None:
                icon_path = dest
            print(f"  candidate {i + 1} ({c['kind']}, score {c['score']}): {dest}")
    raster = [d["path"] for d in downloaded if not d["path"].endswith(".svg")]
    primary_logo = next((d["path"] for d in downloaded
                         if d["kind"] == "img" and not d["path"].endswith(".svg")), None) \
        or (raster[0] if raster else None)

    # colors (CSS + sampled brand-asset colors)
    css = collect_css(data)
    ranked = extract_colors(css, raster)
    palette = propose_palette(ranked)
    preview_path = os.path.join(assets_dir, "palette_preview.png")
    try:
        make_preview(palette, preview_path)
    except Exception as exc:  # preview is a nicety; never fail the run on it
        preview_path = None
        print(f"  (palette preview skipped: {exc})")

    # fonts - download the brand's real font (Google Fonts -> TTF), else a portable fallback
    fonts_detected = extract_fonts(css)
    detected_family = fonts_detected["families_ranked"][0] if fonts_detected["families_ranked"] else None
    chosen_fonts = brand_fonts.ensure_fonts(detected_family, paths["fonts"], allow_google=not args.no_google)
    chosen_fonts["detected_on_site"] = fonts_detected["families_ranked"][:3]

    brand_kit = {
        "source_url": base,
        "company_name": name,
        "logo": {"primary": primary_logo, "icon": icon_path, "candidates": [d["path"] for d in downloaded]},
        "colors": palette,
        "fonts": chosen_fonts,
        "_evidence": {"color_ranked_top": ranked[:12], "fonts_detected": fonts_detected,
                      "logo_candidates": downloaded, "palette_preview": preview_path},
        "_note": "Best-guess auto-extraction. Review colors/logo/fonts and edit before locking.",
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(brand_kit, f, indent=2, ensure_ascii=False)
    common.set_active(slug)

    print(f"\nProposed palette: primary={palette['primary']} secondary={palette['secondary']} "
          f"accent={palette['accent']} dark={palette['dark']} light={palette['light']}")
    print(f"Detected fonts: {fonts_detected['families_ranked']} "
          f"(google={fonts_detected['uses_google_fonts']}) -> brand font: "
          f"{chosen_fonts['family_name']} ({chosen_fonts['source']})")
    print(f"Logo: primary={primary_logo}  icon={icon_path}")
    print(f"Wrote {out_path} (client '{slug}', now active)"
          + (f" + preview {preview_path}" if preview_path else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
