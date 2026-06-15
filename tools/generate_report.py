#!/usr/bin/env python3
"""
generate_report.py - render a branded competitor-analysis PDF from a run snapshot.

Reads:
  data/runs/<date>/snapshot.json   (default: latest run)
  data/runs/<date>/changes.json    (optional; "What changed" section)
  brand_kit.json                   (logo, colors, fonts)
Writes:
  reports/<date>_competitor_report.pdf

Layout: branded cover -> executive summary -> your business snapshot -> market landscape
(+ matplotlib chart) -> per-competitor profiles -> what's working for competitors ->
where you can improve -> what changed since last run -> sources & methodology.

Uses reportlab Platypus with the brand palette/fonts and the company logo in the cover +
running header/footer. Deterministic. WAT `tools/` layer.

CLI:
    python tools/generate_report.py [--date 2026-06-15] [--runs-dir data/runs]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from reportlab.lib.colors import HexColor, white  # noqa: E402
from reportlab.lib.enums import TA_CENTER, TA_LEFT  # noqa: E402
from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.pdfmetrics import registerFontFamily  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (BaseDocTemplate, Frame, Image as RLImage, ListFlowable,  # noqa: E402
                                ListItem, NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)


# ---------------------------------------------------------------- fonts / colors
def register_fonts(fonts: dict) -> dict:
    fam = fonts.get("family_name", "Arial")
    try:
        pdfmetrics.registerFont(TTFont(fam, fonts["regular"]))
        pdfmetrics.registerFont(TTFont(f"{fam}-Bold", fonts["bold"]))
        pdfmetrics.registerFont(TTFont(f"{fam}-Italic", fonts["italic"]))
        pdfmetrics.registerFont(TTFont(f"{fam}-BoldItalic", fonts["bolditalic"]))
        registerFontFamily(fam, normal=fam, bold=f"{fam}-Bold",
                           italic=f"{fam}-Italic", boldItalic=f"{fam}-BoldItalic")
        return {"normal": fam, "bold": f"{fam}-Bold", "italic": f"{fam}-Italic",
                "bolditalic": f"{fam}-BoldItalic"}
    except Exception as exc:  # built-in Helvetica always works
        print(f"  (font registration fell back to Helvetica: {exc})", file=sys.stderr)
        return {"normal": "Helvetica", "bold": "Helvetica-Bold",
                "italic": "Helvetica-Oblique", "bolditalic": "Helvetica-BoldOblique"}


def make_styles(F: dict, C: dict) -> dict:
    primary, accent, dark = HexColor(C["primary"]), HexColor(C["accent"]), HexColor(C["dark"])
    muted = HexColor("#5b6470")
    s = {}
    s["body"] = ParagraphStyle("body", fontName=F["normal"], fontSize=9.5, leading=14,
                               textColor=dark, alignment=TA_LEFT, spaceAfter=4)
    s["muted"] = ParagraphStyle("muted", parent=s["body"], fontSize=8.2, textColor=muted)
    s["italic"] = ParagraphStyle("italic", parent=s["body"], fontName=F["italic"],
                                 fontSize=10, textColor=primary, spaceAfter=6)
    s["h1"] = ParagraphStyle("h1", fontName=F["bold"], fontSize=17, leading=21, textColor=primary,
                             spaceBefore=6, spaceAfter=8, keepWithNext=1)
    s["h2"] = ParagraphStyle("h2", fontName=F["bold"], fontSize=12.5, leading=16, textColor=primary,
                             spaceBefore=10, spaceAfter=3, keepWithNext=1)
    s["label"] = ParagraphStyle("label", fontName=F["bold"], fontSize=8.5, leading=12,
                                textColor=HexColor("#6b7280"))
    s["cell"] = ParagraphStyle("cell", parent=s["body"], fontSize=9, spaceAfter=0)
    s["bullet"] = ParagraphStyle("bullet", parent=s["body"], fontSize=9, leading=13, spaceAfter=2)
    s["kpi"] = ParagraphStyle("kpi", fontName=F["bold"], fontSize=9.5, textColor=accent)
    return s


def esc(text) -> str:
    return escape("" if text is None else str(text))


# ---------------------------------------------------------------- flowable helpers
def section(title, styles, C):
    rule = Table([[""]], colWidths=[1.3 * inch], rowHeights=[3])
    rule.hAlign = "LEFT"
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), HexColor(C["accent"])),
                              ("LINEBELOW", (0, 0), (-1, -1), 0, white)]))
    return [Spacer(1, 10), rule, Paragraph(esc(title), styles["h1"])]


def bullets(items, styles, style_key="bullet", limit=None):
    items = [i for i in (items or []) if str(i).strip()]
    if limit:
        items = items[:limit]
    if not items:
        return Paragraph("<i>None identified.</i>", styles["muted"])
    return ListFlowable([ListItem(Paragraph(esc(i), styles[style_key]), leftIndent=12,
                                  value="•") for i in items],
                        bulletType="bullet", start="•", leftIndent=10)


def kv_table(rows, styles, C, col0=1.35 * inch, total=6.6 * inch):
    data = []
    for label, value in rows:
        if not str(value or "").strip():
            continue
        data.append([Paragraph(esc(label).upper(), styles["label"]),
                     Paragraph(esc(value), styles["cell"])])
    if not data:
        return Spacer(1, 1)
    t = Table(data, colWidths=[col0, total - col0])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, HexColor("#e5e7eb")),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    return t


def competitor_block(c, styles, C):
    flow = [Paragraph(esc(c["name"]), styles["h2"])]
    if c.get("one_liner"):
        flow.append(Paragraph(esc(c["one_liner"]), styles["italic"]))
    flow.append(kv_table([
        ("Positioning", c.get("positioning")),
        ("Target customers", c.get("target_customers")),
        ("Products", ", ".join(c.get("products") or [])),
        ("Pricing", c.get("pricing")),
        ("Customer sentiment", c.get("customer_sentiment")),
        ("Ratings", c.get("ratings")),
    ], styles, C))

    if c.get("key_features"):
        flow += [Spacer(1, 3), Paragraph("Key features", styles["label"]),
                 bullets(c.get("key_features"), styles, limit=8)]

    sw = Table([[
        [Paragraph("STRENGTHS", styles["label"]), bullets(c.get("strengths"), styles, limit=6)],
        [Paragraph("WATCH-OUTS", styles["label"]), bullets(c.get("weaknesses"), styles, limit=6)],
    ]], colWidths=[3.3 * inch, 3.3 * inch])
    sw.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (0, -1), 0),
                            ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
                            ("TOPPADDING", (0, 0), (-1, -1), 6)]))
    flow += [Spacer(1, 2), sw]

    if c.get("recent_moves"):
        flow += [Spacer(1, 3), Paragraph("Recent moves", styles["label"]),
                 bullets(c.get("recent_moves"), styles, limit=6)]
    if c.get("content_marketing") or c.get("social_presence"):
        flow += [Spacer(1, 3), Paragraph("Marketing & channels", styles["label"])]
        if c.get("content_marketing"):
            flow.append(Paragraph(esc(c["content_marketing"]), styles["body"]))
        sp = c.get("social_presence") or {}
        flow.append(kv_table([("LinkedIn", sp.get("linkedin")),
                              ("YouTube", sp.get("youtube")),
                              ("X / other", sp.get("x"))], styles, C))
    if c.get("sources"):
        srcs = " · ".join(esc(s) for s in (c.get("sources") or [])[:6])
        flow.append(Paragraph(f"Sources: {srcs}", styles["muted"]))
    flow.append(Spacer(1, 10))
    return flow


# ---------------------------------------------------------------- chart
def build_chart(competitors, C, path):
    comps = [c for c in competitors if (c.get("strengths") or c.get("weaknesses"))]
    if not comps:
        return None
    names = [c["name"] for c in comps]
    strengths = [len(c.get("strengths") or []) for c in comps]
    weaknesses = [-len(c.get("weaknesses") or []) for c in comps]
    y = range(len(names))
    fig, ax = plt.subplots(figsize=(7.0, 0.5 * len(names) + 1.2), dpi=150)
    ax.barh(y, strengths, color=C["accent"], label="Strengths", height=0.6)
    ax.barh(y, weaknesses, color=C["secondary"], label="Watch-outs", height=0.6)
    ax.set_yticks(list(y))
    ax.set_yticklabels(names, fontsize=9)
    ax.axvline(0, color="#9aa0a6", linewidth=0.8)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(length=0)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=8, frameon=False)
    ax.set_title("Strengths vs. watch-outs identified per competitor", fontsize=11,
                 color=C["primary"], fontweight="bold", loc="left")
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# ---------------------------------------------------------------- page furniture
def make_cover(canvas, doc, ctx):
    C, logo, title, subtitle, date_str = ctx["C"], ctx["logo_white"], ctx["title"], ctx["subtitle"], ctx["date"]
    w, h = doc.pagesize
    canvas.saveState()
    canvas.setFillColor(HexColor(C["primary"]))
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    # gold accent band
    canvas.setFillColor(HexColor(C["accent"]))
    canvas.rect(0, h - 0.55 * inch, w, 0.12 * inch, fill=1, stroke=0)
    # logo (white wordmark) centered upper third
    if logo and os.path.exists(logo):
        try:
            ir = ImageReader(logo)
            iw, ih = ir.getSize()
            disp_w = 2.6 * inch
            disp_h = disp_w * ih / iw
            canvas.drawImage(ir, (w - disp_w) / 2, h - 3.0 * inch, disp_w, disp_h,
                             mask="auto", preserveAspectRatio=True)
        except Exception:
            pass
    canvas.setFillColor(white)
    canvas.setFont(ctx["F"]["bold"], 30)
    canvas.drawCentredString(w / 2, h - 4.4 * inch, "Competitive Landscape Report")
    canvas.setFillColor(HexColor(C["accent"]))
    canvas.setFont(ctx["F"]["normal"], 14)
    canvas.drawCentredString(w / 2, h - 4.85 * inch, subtitle)
    canvas.setFillColor(HexColor("#c7ccd6"))
    canvas.setFont(ctx["F"]["normal"], 11)
    canvas.drawCentredString(w / 2, h - 5.2 * inch, date_str)
    # footer confidential
    canvas.setFillColor(HexColor("#8b93a3"))
    canvas.setFont(ctx["F"]["normal"], 8.5)
    canvas.drawCentredString(w / 2, 0.6 * inch, f"Confidential — prepared for {title}")
    canvas.restoreState()


def make_header_footer(canvas, doc, ctx):
    C, F = ctx["C"], ctx["F"]
    w, h = doc.pagesize
    canvas.saveState()
    # header: monogram + company name
    icon = ctx["logo_icon"]
    x = doc.leftMargin
    if icon and os.path.exists(icon):
        try:
            canvas.drawImage(ImageReader(icon), x, h - 0.72 * inch, 0.26 * inch, 0.26 * inch,
                             mask="auto", preserveAspectRatio=True)
            x += 0.36 * inch
        except Exception:
            pass
    canvas.setFillColor(HexColor(C["primary"]))
    canvas.setFont(F["bold"], 9)
    canvas.drawString(x, h - 0.6 * inch, ctx["title"])
    canvas.setStrokeColor(HexColor(C["accent"]))
    canvas.setLineWidth(1.2)
    canvas.line(doc.leftMargin, h - 0.8 * inch, w - doc.rightMargin, h - 0.8 * inch)
    # footer: page number + label
    canvas.setStrokeColor(HexColor("#e5e7eb"))
    canvas.setLineWidth(0.6)
    canvas.line(doc.leftMargin, 0.68 * inch, w - doc.rightMargin, 0.68 * inch)
    canvas.setFillColor(HexColor("#6b7280"))
    canvas.setFont(F["normal"], 8)
    canvas.drawString(doc.leftMargin, 0.5 * inch, f"Competitive Landscape Report · {ctx['date']}")
    canvas.drawRightString(w - doc.rightMargin, 0.5 * inch, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


# ---------------------------------------------------------------- main build
def latest_run(runs_dir):
    runs = [d for d in os.listdir(runs_dir)
            if os.path.isfile(os.path.join(runs_dir, d, "snapshot.json"))] if os.path.isdir(runs_dir) else []
    return sorted(runs)[-1] if runs else None


def load(path, default=None):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def build(snapshot, changes, brand, out_path):
    C = brand["colors"]
    F = register_fonts(brand.get("fonts", {}))
    styles = make_styles(F, C)
    biz = snapshot.get("business", {})
    date_str = snapshot.get("run_date", datetime.date.today().isoformat())
    title = biz.get("name", "Your Company")
    ctx = {"C": C, "F": F, "title": title,
           "subtitle": biz.get("tagline") or biz.get("category") or "Market & Competitor Intelligence",
           "date": date_str,
           "logo_white": (brand.get("logo") or {}).get("primary"),
           "logo_icon": (brand.get("logo") or {}).get("icon")}

    doc = BaseDocTemplate(out_path, pagesize=LETTER, leftMargin=0.85 * inch,
                          rightMargin=0.85 * inch, topMargin=1.0 * inch, bottomMargin=0.9 * inch,
                          title=f"Competitive Landscape Report — {title}", author=title)
    cover_frame = Frame(0, 0, *doc.pagesize, id="cover")
    content_frame = Frame(doc.leftMargin, doc.bottomMargin,
                          doc.width, doc.height, id="content")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=lambda c, d: make_cover(c, d, ctx)),
        PageTemplate(id="content", frames=[content_frame], onPage=lambda c, d: make_header_footer(c, d, ctx)),
    ])

    story = [NextPageTemplate("content"), Spacer(1, 2), PageBreak()]

    ms = snapshot.get("market_summary", {})
    comps = snapshot.get("competitors", [])

    # Executive summary
    story += section("Executive Summary", styles, C)
    if ms.get("overview"):
        story.append(Paragraph(esc(ms["overview"]), styles["body"]))
    story.append(Paragraph(f"<b>{len(comps)} competitors</b> tracked: "
                           f"{esc(', '.join(c['name'] for c in comps))}.", styles["body"]))
    if changes and changes.get("summary") and not changes.get("baseline"):
        story += [Spacer(1, 4), Paragraph("Headline changes since last run", styles["label"]),
                  bullets(changes["summary"], styles, limit=6)]
    if ms.get("key_trends"):
        story += [Spacer(1, 4), Paragraph("Key market trends", styles["label"]),
                  bullets(ms["key_trends"], styles, limit=6)]

    # Your business snapshot
    story += section(f"Your Business — {title}", styles, C)
    story.append(kv_table([
        ("What you do", biz.get("description") or biz.get("value_prop")),
        ("Category", biz.get("category")),
        ("Target customers", biz.get("target_customers")),
        ("Value proposition", biz.get("value_prop")),
        ("Differentiators", ", ".join(biz.get("differentiators") or []) if isinstance(biz.get("differentiators"), list) else biz.get("differentiators")),
        ("Website", biz.get("url")),
    ], styles, C))

    # Market landscape + chart
    story += section("Market Landscape", styles, C)
    land = [[Paragraph("COMPETITOR", styles["label"]), Paragraph("POSITIONING", styles["label"])]]
    for c in comps:
        land.append([Paragraph(f"<b>{esc(c['name'])}</b>", styles["cell"]),
                     Paragraph(esc(c.get("one_liner") or c.get("positioning") or ""), styles["cell"])])
    lt = Table(land, colWidths=[1.7 * inch, 4.9 * inch], repeatRows=1)
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor(C["primary"])),
        ("TEXTCOLOR", (0, 0), (-1, 0), white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor(C["light"])]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#e5e7eb")),
    ]))
    story.append(lt)
    chart = build_chart(comps, C, os.path.join(".tmp", "chart_strengths.png"))
    if chart:
        story += [Spacer(1, 10), RLImage(chart, width=6.4 * inch,
                                         height=6.4 * inch * _img_ratio(chart))]

    # Marketing & channel presence (comparative)
    if any(c.get("social_presence") or c.get("content_focus") for c in comps):
        story += section("Marketing & Channel Presence", styles, C)
        ch_rows = [[Paragraph("COMPETITOR", styles["label"]), Paragraph("LINKEDIN", styles["label"]),
                    Paragraph("YOUTUBE", styles["label"]), Paragraph("CONTENT FOCUS", styles["label"])]]
        for c in comps:
            sp = c.get("social_presence") or {}
            ch_rows.append([Paragraph(f"<b>{esc(c['name'])}</b>", styles["cell"]),
                            Paragraph(esc(sp.get("linkedin") or "—"), styles["cell"]),
                            Paragraph(esc(c.get("youtube_activity") or "—"), styles["cell"]),
                            Paragraph(esc(c.get("content_focus") or ""), styles["cell"])])
        ct = Table(ch_rows, colWidths=[1.45 * inch, 1.45 * inch, 1.5 * inch, 2.2 * inch], repeatRows=1)
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), HexColor(C["primary"])),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor(C["light"])]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#e5e7eb")),
        ]))
        story.append(ct)
        channel_note = ms.get("channel_gap_note") or ms.get("ivp_channel_note")
        if channel_note:
            story += [Spacer(1, 6), Paragraph(f"<b>Your channel gap:</b> {esc(channel_note)}",
                                              styles["body"])]

    # Per-competitor profiles
    story += section("Competitor Profiles", styles, C)
    for c in comps:
        story += competitor_block(c, styles, C)

    # What's working for them
    if ms.get("whats_working_for_competitors"):
        story += section("What's Working for Competitors", styles, C)
        story.append(bullets(ms["whats_working_for_competitors"], styles))

    # Where you can improve
    opps = ms.get("your_opportunities") or []
    if opps:
        story += section("Where You Can Improve", styles, C)
        for o in opps:
            if isinstance(o, dict):
                pr = (o.get("priority") or "").upper()
                badge = f'  <font color="{C["accent"]}">[{esc(pr)}]</font>' if pr else ""
                story.append(Paragraph(f"<b>{esc(o.get('title',''))}</b>{badge}", styles["body"]))
                if o.get("rationale"):
                    story.append(Paragraph(esc(o["rationale"]), styles["muted"]))
                story.append(Spacer(1, 4))
            else:
                story.append(Paragraph(f"• {esc(o)}", styles["body"]))

    # What changed
    story += section("What Changed Since Last Run", styles, C)
    if changes:
        if changes.get("baseline"):
            story.append(Paragraph("This is the <b>baseline run</b> — the first snapshot of the "
                                   "market. Future runs will highlight what changed here.", styles["body"]))
        else:
            story.append(Paragraph(f"Compared against the run on {esc(changes.get('previous_date'))}.",
                                   styles["muted"]))
            story.append(bullets(changes.get("summary"), styles))
    else:
        story.append(Paragraph("No change data available for this run.", styles["muted"]))

    # Sources & methodology
    story += section("Sources & Methodology", styles, C)
    story.append(Paragraph(esc(snapshot.get("methodology_note", "")), styles["muted"]))
    if snapshot.get("sources"):
        story.append(bullets(snapshot["sources"], styles, style_key="muted", limit=40))

    doc.build(story)
    return out_path


def _img_ratio(path):
    from PIL import Image
    with Image.open(path) as im:
        return im.height / im.width


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render the branded competitor-analysis PDF.")
    ap.add_argument("--date", default=None, help="Run date to render (default: latest)")
    common.add_client_arg(ap)
    ap.add_argument("--out", default=None, help="Output PDF path (default: clients/<slug>/reports/...)")
    args = ap.parse_args(argv)

    slug = common.resolve_slug(args.client)
    paths = common.client_paths(slug)
    date = args.date or latest_run(paths["runs"])
    if not date:
        print(f"No snapshot found to render for client '{slug}'.", file=sys.stderr)
        return 1
    run_dir = os.path.join(paths["runs"], date)
    snapshot = load(os.path.join(run_dir, "snapshot.json"))
    if not snapshot:
        print(f"No snapshot.json in {run_dir}", file=sys.stderr)
        return 1
    changes = load(os.path.join(run_dir, "changes.json"))
    brand = load(paths["brand_kit"])
    if not brand:
        print(f"No brand kit at {paths['brand_kit']}", file=sys.stderr)
        return 1

    os.makedirs(paths["reports"], exist_ok=True)
    os.makedirs(".tmp", exist_ok=True)
    out = args.out or os.path.join(paths["reports"], f"{slug}_{date}_competitor_report.pdf")
    build(snapshot, changes, brand, out)
    size = os.path.getsize(out)
    print(f"Wrote {out}  ({size // 1024} KB, {len(snapshot.get('competitors', []))} competitors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
