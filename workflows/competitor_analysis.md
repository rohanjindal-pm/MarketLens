# Workflow: Competitor Analysis & Market Monitoring

## Objective
Produce a **branded PPTX deck** that maps the company's competitive landscape, surfaces what's working
for competitors and where the company can improve, and is **re-runnable** so each run highlights
what changed since the last one.

**Multi-client:** each target company is a "client" with an isolated folder `clients/<slug>/`
(brand, profile, run history, reports). The active client lives in `config.json`; every tool takes
`--client <slug>`. The report adopts each client's real **colors, fonts, and logo**, while the
**section structure/headers stay identical**. (First client: `ivp` — Indus Valley Partners, ivp.in.)

## Inputs
- A company **website URL** + a **client slug** (e.g. `--client acme`).
- Per-client config produced on first run, reused after:
  `clients/<slug>/{business_profile.json, brand_kit.json, brand/ (logo + fonts)}`.

## Outputs (per client)
- `clients/<slug>/reports/<slug>_<YYYY-MM-DD>_competitor_report.pptx` — the deliverable (portrait 8.5×11 slides, the LETTER aspect ratio; sources cited per paragraph/table).
- `clients/<slug>/data/runs/<YYYY-MM-DD>/snapshot.json` + `changes.json` — durable history (change tracking).

## The WAT split (important)
Research uses **free** built-in `WebSearch` / `WebFetch` (no paid Apify, no API keys). Those are
**agent** tools — the deterministic Python tools do NOT search the web. So:
- **You (the agent)** do: discovery, searching, judging relevance, and synthesis.
- **`tools/`** do: fetching/parsing specific URLs, brand extraction, validation, storage, diffing, PDF rendering.

## Tools
All client-aware tools take `--client <slug>` (default: `active_client` in `config.json`).
| Tool | Role |
|------|------|
| `tools/use_client.py [slug]` | List clients / set the active client. |
| `tools/new_client.py <url> --slug <slug>` | **One-command scaffold**: branding + brand font + profile material (`.tmp/<slug>_site.txt`) + `business_profile.json` stub; sets active. |
| `tools/fetch_site.py <url>` | Fetch + clean a page (HTML, text, links, CSS, images, icons) → `.tmp/`. Shared helper. |
| `tools/extract_brand.py <url> --client <slug>` | Infer logo/palette/fonts + **download the brand's real font** → `clients/<slug>/brand_kit.json` + `brand/`. Sets active client. **Confirm with user.** |
| `tools/brand_fonts.py --client <slug> [--family X]` | (Re)install a client's brand font: Google Fonts → TTF, else portable DejaVu. |
| `tools/save_snapshot.py <findings.json> --client <slug> [--date D]` | Validate findings → `clients/<slug>/data/runs/<D>/snapshot.json`. |
| `tools/diff_snapshots.py --client <slug>` | Diff the two latest snapshots → `clients/<slug>/data/runs/<latest>/changes.json`. |
| `tools/generate_report_pptx.py --client <slug> [--date D]` | Render the branded PPTX deck (client's colors/fonts/logo; same structure; sources cited per section). |

Run everything in the project venv: `uv run python tools/<tool>.py …`

## Run order (SOP)

0. **Scaffold the client (one command)** — `uv run python tools/new_client.py <url> --slug <slug>` runs
   branding + brand-font download, saves profile material to `.tmp/<slug>_site.txt`, writes a
   `business_profile.json` stub, and sets the client active. Switch among existing clients with
   `tools/use_client.py <slug>`. All later steps pass `--client <slug>`. (Steps 1–2 then *confirm/refine*.)
1. **Confirm branding** — `new_client` (step 0) already ran `extract_brand` (palette + logo + the brand's
   real font via Google Fonts → TTF, DejaVu fallback). Open `clients/<slug>/brand/palette_preview.png` +
   logo candidates; **present to the user and confirm** colors/logo/fonts (edit `brand_kit.json`
   `colors`/`fonts`). To redo branding manually: `uv run python tools/extract_brand.py <url> --client <slug>`.
2. **Business profile** — draft `clients/<slug>/business_profile.json` (`name, url, tagline, category,
   description, value_prop, target_customers, differentiators[]`) from the gathered `.tmp/<slug>_site.txt`
   (or `fetch_site.py` more pages). **Have the user review/augment**, then save. Skip if already confirmed.
3. **Discover competitors** — use `WebSearch` ("competitors of / alternatives to <company>", "<category>
   vendors"). Propose a shortlist of ~5–7 and **get user approval** before researching.
4. **Research each competitor** — for each: `WebSearch` + `WebFetch`/`fetch_site.py` across the
   **dimensions** below. Fill one competitor object per the schema. Prefer primary sources (their site,
   pricing page) + reviews (G2/Capterra/Trustpilot) + recent news. Record `sources[]`.
5. **Assemble findings** — write the full findings object (business + market_summary + competitors) to
   `.tmp/findings.json`.
6. **Persist** — `uv run python tools/save_snapshot.py .tmp/findings.json --client <slug>`. Fix any schema errors.
7. **Diff** — `uv run python tools/diff_snapshots.py --client <slug>` (baseline on first run).
8. **Render** — `uv run python tools/generate_report_pptx.py --client <slug>`. Present `clients/<slug>/reports/<slug>_<date>_competitor_report.pptx`.

## Findings / snapshot schema (the data contract)
`save_snapshot.py` expects this shape (it fills sensible defaults for missing competitor fields):
```json
{
  "business": { "name", "url", "tagline", "category", "description",
                "value_prop", "target_customers", "differentiators": [] },
  "market_summary": {
    "overview": "1–2 paragraphs on the state of the market",
    "key_trends": ["…"],
    "whats_working_for_competitors": ["theme → why it works"],
    "your_opportunities": [ {"title", "rationale", "priority": "high|medium|low"} ],
    "channel_gap_note": "how the client's own channels compare (renders as 'Your channel gap')"
  },
  "competitors": [ {
    "name", "url", "one_liner", "positioning", "target_customers",
    "products": [], "key_features": [], "pricing": "summary or 'Not publicly listed'",
    "strengths": [], "weaknesses": [], "marketing_content", "customer_sentiment",
    "ratings": "G2/Capterra/Gartner (product) or Glassdoor (employer) — label which",
    "content_focus": "short label for the channel table",
    "youtube_activity": "e.g. '~700 subs; active (2026)'",
    "social_presence": { "linkedin": "", "youtube": "", "x": "" },
    "recent_moves": ["dated: funding / launches / partnerships / hires"],
    "sources": ["url", "url"]
  } ]
}
```

## Analysis dimensions (per competitor)
Positioning & messaging · Products/features · Pricing & packaging · Customer sentiment & ratings (G2/Capterra/Gartner) ·
**Marketing channels** (LinkedIn, **YouTube** content + cadence, podcasts, webinars, events) · Growth signals (hiring, funding, M&A, news).
For YouTube, capture the channel, what they publish, and how recent/active it is — then contrast with the user's own channel.

## Re-running & change tracking
Re-run steps 3–8 anytime (always with `--client <slug>`). `save_snapshot.py` stamps today's date (override with `--date`).
`diff_snapshots.py` compares the two most recent runs and the report's **"What Changed Since Last Run"**
section reflects it. Branding + profile (steps 1–2) are reused, so re-runs are fast.

## Edge cases & lessons learned (keep this current)
- **Free-only:** never call paid Apify here; use `WebSearch`/`WebFetch`/`requests`.
- **Brand fonts:** `extract_brand.py` (via `brand_fonts.py`) downloads the brand's **real font** from
  Google Fonts as TTF (old-User-Agent trick) into `clients/<slug>/brand/fonts/`, falling back to
  **DejaVu Sans** (bundled with matplotlib; portable) when the font isn't on Google Fonts. Override with
  `brand_fonts.py --client <slug> --family "<Name>"`, or drop a `.ttf` in `brand/fonts/` and edit
  `brand_kit.json → fonts`. (The canvas-design bundled TTFs are corrupt here — don't use them.)
- **Next.js sites** wrap images as `/_next/image?url=…`; `extract_brand.py` decodes these.
- **Client-logo walls** (e.g. `/wp-content/uploads/`, `*.wpengine.com`) are down-ranked so a customer's
  logo isn't mistaken for the company's own. Favicons / apple-touch-icon are trusted brand marks.
- **Logo color:** a white wordmark (for dark backgrounds) yields no sampled color — confirm palette by eye.
- **User confirmation checkpoints:** branding (step 1), business profile (step 2), competitor shortlist
  (step 3). Don't skip these — auto-extraction is a starting point, not the final word.
- **YouTube/social channels:** YouTube channel pages are JS-rendered (WebFetch/requests see only the footer). To read what a competitor publishes, resolve the channel ID from the channel-page HTML (`"browseId":"UC…"`) or from the homepage's social links, then fetch the public RSS feed `https://www.youtube.com/feeds/videos.xml?channel_id=UC…` for recent video titles + dates. Find a site's social links by regexing the raw homepage HTML for youtube/linkedin/x.com (note: `fetch_site.py` keeps only same-domain links, so use a raw `requests` grep for off-domain socials). WebSearch can rate-limit — these direct reads don't.
- **Deck preview:** to eyeball the `.pptx`, convert to PDF with LibreOffice (`brew install --cask libreoffice`, provides `soffice`) then render pages with `pymupdf`:
  `soffice --headless --convert-to pdf --outdir .tmp clients/<slug>/reports/<file>.pptx && uv run python -c "import fitz,glob; [p.get_pixmap(dpi=110).save(f'.tmp/p{i}.png') for i,p in enumerate(fitz.open(glob.glob('.tmp/*competitor_report.pdf')[0]))]"`.
  (python-pptx can't render; fonts fall back to a system sans in this preview, but layout/overflow are accurate.)

## Constraints
Deliverable = the `.pptx` deck in `reports/`. `.tmp/` is disposable. `data/`, `business_profile.json`,
`brand_kit.json`, `brand/` are durable — do not delete between runs (they hold history + config).
