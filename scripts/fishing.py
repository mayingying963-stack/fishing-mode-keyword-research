#!/usr/bin/env python3
"""捕鱼法 / Fishing-Mode keyword research — SEO小平 methodology, end to end.

Five steps, exactly as in the original SOP:

  01 以词找词  seed keywords -> keyword-ideas expansion
  02 用词找站  top-volume relevant keywords -> Google top-50 organic SERP -> filter junk domains
  03 用站找词  each surviving domain -> its top-100 ranked keywords
  04 交叉去重  merge 01 + 03, keep keyword / intent / volume / difficulty -> sheet3
  05 筛选利基  filter sheet3 down to the niche you can actually rank in

Two run modes:
  api      DataForSEO REST API (needs DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD)
  offline  reads CSV/XLSX you exported by hand (SEO Minion, DataForSEO dashboard, Semrush)

Usage:
  python fishing.py --seeds "forklift battery" "lithium forklift battery" --mode api
  python fishing.py --seeds-file seeds.txt --mode api --serp-targets 20 --out out/
  python fishing.py --offline serp_dir=exports/serp ideas=exports/ideas.csv --mode offline

Every step writes its own file so you can stop, inspect, and resume.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

BASE = "https://api.dataforseo.com/v3/"

# --- defaults tuned for the SOP ------------------------------------------------
SEED_IDEAS_LIMIT = 500     # step 1: candidate keywords per run (filtered down later)
SHEET1_KEEP = 30           # step 2: keep the 30 best related+hvol keywords
SERP_DEPTH = 50            # step 3: top-50 organic results per keyword
DOMAIN_KW_LIMIT = 100      # step 4: top-100 ranked keywords per domain

# Step 2's explicit exclusion list from the SOP, plus the usual suspects that
# are never a product competitor. Extend with --extra-exclude.
JUNK_DOMAINS = {
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.ca", "amazon.com.au",
    "walmart.com", "youtube.com", "youtu.be", "ebay.com", "aliexpress.com",
    "alibaba.com", "made-in-china.com", "globalsources.com", "dhgate.com",
    "temu.com", "wish.com", "etsy.com", "target.com", "homedepot.com",
    "lowes.com", "costco.com", "bestbuy.com", "indiamart.com", "tradeindia.com",
    "facebook.com", "instagram.com", "pinterest.com", "tiktok.com", "x.com",
    "twitter.com", "linkedin.com", "reddit.com", "quora.com", "wikipedia.org",
    "google.com", "bing.com", "googleusercontent.com", "wordpress.org",
    "zhihu.com", "baidu.com", "sohu.com", "163.com", "qq.com", "bilibili.com",
}
# Marketplaces / directories / forums / publishers: kept separate from "real
# competitor" so you can re-admit them with --keep-marketplaces if wanted.
MARKETPLACE_HINTS = (
    "amazon.", "ebay.", "walmart.", "aliexpress.", "alibaba.", "temu.",
    "dhgate.", "etsy.", "wish.", "shopify.com", "indiamart.", "made-in-china.",
)

STOP_TOKENS = {"for", "of", "in", "the", "a", "an", "to", "and", "on", "with", "vs"}


# ------------------------------------------------------------------ utilities
def log(msg: str) -> None:
    print(msg, flush=True)


def bare(domain: str | None) -> str:
    """example.com — strip scheme, www, port, path."""
    d = (domain or "").strip().lower()
    if "://" in d:
        d = urlparse(d).netloc
    d = d.split("/")[0].split(":")[0]
    return d[4:] if d.startswith("www.") and d.count(".") >= 2 else d


def domain_of(url: str) -> str:
    return bare(url)


def base_domain(host: str) -> str:
    """Reduce co.uk / com.au style hosts to their registrable tail for matching."""
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    two_level = {"co.uk", "com.au", "co.nz", "co.za", "com.br", "com.mx", "co.jp",
                 "com.cn", "com.sg", "co.in", "com.tr", "com.my", "com.ph"}
    if ".".join(parts[-2:]) in two_level and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_junk(host: str, extra: set[str]) -> str | None:
    """Return a reason string if this domain should be dropped, else None."""
    h = base_domain(bare(host))
    if not h:
        return "empty"
    if h in JUNK_DOMAINS or h in extra:
        return "blocklist"
    if h.startswith("amazon.") or h.startswith("ebay.") or h.startswith("walmart."):
        return "marketplace"
    if any(hint in h for hint in MARKETPLACE_HINTS):
        return "marketplace"
    return None


def to_int(v) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    s = re.sub(r"[^0-9.]", "", str(v))
    try:
        return int(float(s))
    except ValueError:
        return 0


def to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("%", "").strip())
    except ValueError:
        return None


def norm_kw(k: str) -> str:
    """Normalise a keyword for duplicate detection.

    Handles the plural forms that actually collide in this data:
      batteries -> battery   (-ies -> -y, not naive -s stripping)
      batteries' -> battery
      batteries/battery      -> battery
    Naive `word[:-1]` turns "batteries" into "batterie" while "battery" stays
    "battery", so the pair never merged — a real dedupe bug caught by selftest.
    """
    words = "".join(c if c.isalnum() else " " for c in (k or "").lower()).split()
    stems = set()
    for w in words:
        if w in STOP_TOKENS:
            continue
        if len(w) > 4 and w.endswith("ies"):
            stems.add(w[:-3] + "y")          # batteries -> battery
        elif len(w) > 4 and w.endswith(("sses", "shes", "ches", "xes")):
            stems.add(w[:-2])                # classes -> class
        elif len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            stems.add(w[:-1])                # carts -> cart
        else:
            stems.add(w)
    return " ".join(sorted(stems)) or " ".join(words)


INTENT_ABBR = {"informational": "I", "navigational": "N", "commercial": "C",
               "transactional": "T", "I": "I", "N": "N", "C": "C", "T": "T"}


def norm_intent(v) -> str:
    s = str(v or "").strip().lower()
    return INTENT_ABBR.get(s, s.upper()[:1] or "")


def relevant(keyword: str, seeds: list[str], extra_terms: list[str]) -> bool:
    """SOP step 4: keep only keywords belonging to the initial product.

    Plain token overlap is too weak — every seed contains the category noun
    ("battery"), so "car battery" / "solar battery" would pass on that one token
    and flood sheet1 with off-topic head terms. Instead require a *phrase-level*
    match:

      1. an --context term appears verbatim, or
      2. a seed's head phrase (all its tokens, order-free) is contained in the
         keyword — "lithium forklift battery" matches "48v lithium forklift
         battery price", and "forklift battery" matches "forklift battery charger", or
      3. the keyword shares a discriminating modifier token with a seed — a seed
         token that is *not* the trailing category noun (forklift, lithium, 48v).

    The trailing noun of each seed ("battery") is what defines the category, so
    it never counts on its own.
    """
    k = (keyword or "").lower()
    if not k:
        return False

    STOP = {"for", "of", "in", "the", "a", "an", "to", "and", "on", "with", "vs"}
    kset = set(re.findall(r"[a-z0-9]+", k))

    seed_tok_lists = []
    for s in seeds:
        stoks = [w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP]
        if stoks:
            seed_tok_lists.append(stoks)
    if not seed_tok_lists:
        return True

    # A token present in EVERY seed is the industry qualifier ("forklift" here):
    # without it the keyword is a different product. Tokens present in only some
    # seeds ("lithium", "48v", "charger") are optional variants, not requirements.
    freq: dict[str, int] = {}
    for toks in seed_tok_lists:
        for w in set(toks):
            freq[w] = freq.get(w, 0) + 1
    required = {w for w, c in freq.items() if c == len(seed_tok_lists) and len(w) > 2}
    # The category noun is the required token that also appears last in most seeds.
    tail: dict[str, int] = {}
    for toks in seed_tok_lists:
        tail[toks[-1]] = tail.get(toks[-1], 0) + 1
    category = {w for w, c in tail.items() if c >= max(2, (len(seed_tok_lists) + 1) // 2)}
    if not category:
        category = required

    # An --context term can rescue an on-topic keyword that spells the product
    # differently ("scissor lift battery" vs a forklift seed), but it must not
    # bypass the category noun: "forklift certified" contains the context term
    # "forklift" and is still a training course, not a battery.
    ctx_hit = any(t and t.lower() in k for t in extra_terms)
    has_category = bool(category & kset) if category else True
    if ctx_hit and has_category:
        return True

    # (2) head phrase of any seed contained in the keyword (token-set containment,
    # order-free so "battery forklift" and "forklift battery" both match).
    for toks in seed_tok_lists:
        if set(toks) <= kset:
            return True

    # (3) Otherwise the keyword must still satisfy the two things that define the
    # product: every required industry token, plus the category noun. A variant
    # modifier is a bonus, not a requirement — "lithium battery" and "48v battery"
    # fail because they lack "forklift".
    if required and not (required <= kset):
        return False
    if category and not (category & kset):
        return False
    return True


# ------------------------------------------------------------------ DataForSEO
class DataForSEO:
    def __init__(self) -> None:
        self.login = os.getenv("DATAFORSEO_LOGIN") or os.getenv("DATAFORSEO_USERNAME")
        self.password = os.getenv("DATAFORSEO_PASSWORD")
        self.spend = 0.0
        self.calls = 0
        self.failed = 0

    @property
    def available(self) -> bool:
        return bool(self.login and self.password)

    def post(self, path: str, task: dict, retries: int = 2) -> dict | None:
        if not self.available:
            return None
        auth = (self.login, self.password)
        for attempt in range(retries + 1):
            try:
                r = requests.post(BASE + path, auth=auth, json=[task], timeout=180)
                data = r.json()
                self.calls += 1
                self.spend += float(data.get("cost") or 0)
                t = (data.get("tasks") or [{}])[0]
                code = t.get("status_code") or 0
                if r.status_code != 200 or code >= 40000:
                    msg = f"{code} {t.get('status_message') or data.get('status_message')}"
                    # 40204/40205 = spam or bad credentials: retrying will not help.
                    if code in (40104, 40200, 40204, 40205):
                        raise RuntimeError(msg)
                    if attempt == retries:
                        raise RuntimeError(msg)
                    time.sleep(3 * (attempt + 1))
                    continue
                return (t.get("result") or [None])[0] or {}
            except RuntimeError:
                raise
            except Exception as err:  # noqa: BLE001
                if attempt == retries:
                    self.failed += 1
                    log(f"  ! {path} failed: {type(err).__name__}: {str(err)[:140]}")
                    return None
                time.sleep(3 * (attempt + 1))
        return None

    def report(self) -> None:
        log(f"DataForSEO: {self.calls} calls, ${self.spend:.4f}, {self.failed} failed")


def kw_row(item: dict, source: str = "") -> dict:
    """Flatten a DataForSEO keyword item (Labs or SERP shape) into 4 SOP params."""
    kd = item.get("keyword_data") or item
    info = kd.get("keyword_info") or {}
    props = kd.get("keyword_properties") or {}
    intent = ((kd.get("search_intent_info") or {}).get("main_intent"))
    if intent is None:
        intent = (item.get("search_intent_info") or {}).get("main_intent")
    return {
        "keyword": (kd.get("keyword") or "").strip(),
        "intent": norm_intent(intent),
        "volume": to_int(info.get("search_volume")),
        "difficulty": to_int(props.get("keyword_difficulty")),
        "source": source,
    }


# ------------------------------------------------------------------ steps
def step1_ideas(dfs: DataForSEO, seeds: list[str], loc: int, lang: str,
                limit: int = SEED_IDEAS_LIMIT,
                use_ideas: bool = True) -> list[dict]:
    """01 以词找词 — expand seeds into candidate keywords.

    `keyword_suggestions` is the primary source: it stays on-topic per seed and
    returns real volume for the seed itself. `keyword_ideas` is an associative
    broaden and drifts badly (seeding "forklift battery" returned smoke-detector
    and car-battery terms with 200k volume), so its rows still pass through the
    relevance filter and are merged behind the suggestions.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    def add(items: list[dict], source: str) -> None:
        for it in items:
            row = kw_row(it, source)
            nk = norm_kw(row["keyword"])
            if row["keyword"] and nk not in seen:
                seen.add(nk)
                rows.append(row)

    # One call per seed keeps each seed's context; a combined call loses it.
    def one(seed: str):
        res = dfs.post("dataforseo_labs/google/keyword_suggestions/live", {
            "keyword": seed, "location_code": loc, "language_code": lang,
            "limit": 100, "include_seed_keyword": True,
        })
        return seed, res

    with ThreadPoolExecutor(max_workers=5) as pool:
        for seed, res in pool.map(one, seeds):
            add((res or {}).get("items") or [], f"suggest:{seed}")

    if use_ideas:
        res = dfs.post("dataforseo_labs/google/keyword_ideas/live", {
            "keywords": seeds, "location_code": loc, "language_code": lang,
            "limit": limit, "include_serp_info": False,
        })
        add((res or {}).get("items") or [], "ideas")

    # Any seed the API never echoed back is added as a bare row, not a fake one.
    for s in seeds:
        if norm_kw(s) not in seen:
            seen.add(norm_kw(s))
            rows.append({"keyword": s.strip(), "intent": "", "volume": 0,
                         "difficulty": 0, "source": "seed"})
    rows.sort(key=lambda r: (r["volume"], -r["difficulty"]), reverse=True)
    return rows


def step2_sheet1(ideas: list[dict], seeds: list[str], extra_terms: list[str],
                 keep: int = SHEET1_KEEP) -> list[dict]:
    """02 用词找站 (part 1) — the 30 related, highest-traffic keywords -> sheet1."""
    filtered = [r for r in ideas if relevant(r["keyword"], seeds, extra_terms)]
    filtered.sort(key=lambda r: (r["volume"], -(r["difficulty"] or 0)), reverse=True)
    return filtered[:keep]


DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*(\.[a-z0-9\-]+)+$", re.I)


def looks_like_domain(host: str) -> bool:
    """A real registrable host, not YouTube's '8.5k+ views · 6 years ago' breadcrumb."""
    h = (host or "").strip().lower()
    if not h or " " in h or "·" in h or "›" in h:
        return False
    if not DOMAIN_RE.match(h):
        return False
    return "." in h and len(h.split(".")[-1]) >= 2


def domain_from_serp_item(it: dict) -> str:
    """Extract the real result domain from a DataForSEO SERP item.

    Google's current layout makes DataForSEO emit `domain: "google.com"` and
    `url: "https://google.com/goto?url=CAES..."` for every organic result — the
    redirect wrapper is all that survives in those two fields. The real site only
    appears in `breadcrumb`, and its shape varies:

        "wilsonbarrett.com › product-category › forklift-..."      (bare host)
        "https://www.copowbattery.com › News"                      (scheme + host)
        "8.5k+ views · 6 years ago"                                (a YouTube video)

    So strip the scheme, cut at the breadcrumb separator, and validate the result
    as a hostname. If nothing parses, fall back to `website_name` — and only
    accept it when it already looks like a domain, otherwise a channel name like
    "RoyPow" becomes a fake target and step 03 dies on `Invalid Field: target`.
    """
    crumbs = it.get("breadcrumb") or ""
    candidates = [
        crumbs.split("›")[0],
        crumbs.split("·")[0],
        domain_of(it.get("url") or ""),
        it.get("domain") or "",
    ]
    for candidate in candidates:
        host = (candidate or "").strip()
        host = re.sub(r"^[a-z]+://", "", host, flags=re.I)   # drop scheme
        host = host.split("/")[0].split("?")[0].strip().strip(".")
        if "google." in host.lower():
            continue
        if looks_like_domain(host):
            return base_domain(host)

    name = (it.get("website_name") or "").strip()
    if looks_like_domain(name):
        return base_domain(name)
    return ""


def serp_top_domains(dfs: DataForSEO, keywords: list[str], loc: int, lang: str,
                     depth: int = SERP_DEPTH, exclude: set[str] | None = None
                     ) -> tuple[list[dict], list[dict]]:
    """02 用词找站 (part 2) — top-N organic per keyword, junk removed.

    Returns (per-keyword hit rows, per-domain aggregate).
    """
    exclude = exclude or set()
    hits: list[dict] = []
    dropped: list[dict] = []

    def one(kw: str):
        # No max_crawl_pages: it truncates parsing and starves the item list.
        # 40101 (internal SE server error) is Google-side and transient, so the
        # per-call retries in post() handle it; a keyword that still fails is
        # skipped rather than aborting the other 29.
        for attempt in range(3):
            try:
                res = dfs.post("serp/google/organic/live/advanced",
                               {"keyword": kw, "location_code": loc, "language_code": lang,
                                "depth": depth})
                return kw, res
            except RuntimeError as err:
                msg = str(err)
                if "40101" in msg or "Internal SE" in msg:
                    log(f"  ~ SERP transient error for '{kw}' (try {attempt + 1}/3): {msg}")
                    time.sleep(5 * (attempt + 1))
                    continue
                log(f"  ! SERP failed for '{kw}': {msg}")
                return kw, None
        log(f"  ! SERP gave up on '{kw}' after 3 tries")
        return kw, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, keywords))

    for kw, res in results:
        for it in (res or {}).get("items") or []:
            if it.get("type") != "organic":
                continue
            host = domain_from_serp_item(it)
            if not host:
                continue
            row = {"keyword": kw, "position": it.get("rank_group") or it.get("rank_absolute"),
                   "domain": host,
                   "url": it.get("breadcrumb") or it.get("url") or "",
                   "title": it.get("title") or ""}
            reason = is_junk(host, exclude)
            if reason:
                dropped.append(row | {"reason": reason})
            else:
                hits.append(row)

    return hits, collect_domains(hits)


def step3_domain_keywords(dfs: DataForSEO, domains: list[str], loc: int, lang: str,
                          limit: int = DOMAIN_KW_LIMIT,
                          seeds: list[str] | None = None,
                          extra_terms: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """03 用站找词 — each competitor's top-N ranked keywords, off-topic removed.

    SOP step 5 filters these before sheet2: a competitor selling forklift batteries
    usually also ranks for solar and golf-cart batteries, and those must not leak
    into the final table. The same relevance rule as sheet1 applies here.
    """
    seeds = seeds or []
    extra_terms = extra_terms or []

    def one(dom: str):
        try:
            res = dfs.post("dataforseo_labs/google/ranked_keywords/live", {
                "target": dom, "location_code": loc, "language_code": lang,
                "limit": limit, "order_by": ["keyword_data.keyword_info.search_volume,desc"],
                "include_serp_info": False,
            })
        except RuntimeError as err:
            # One bad target must not abort the batch.
            log(f"  ! ranked_keywords failed for {dom}: {err}")
            res = None
        return dom, res

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, domains))

    rows, per_domain = [], []
    for dom, res in results:
        items = (res or {}).get("items") or []
        got, kept = 0, 0
        for it in items:
            got += 1
            row = kw_row(it, dom)
            if not row["keyword"]:
                continue
            if seeds and not relevant(row["keyword"], seeds, extra_terms):
                continue
            se = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
            row["position"] = se.get("rank_group")
            # SHEET_HEADERS["sheet2"] names this column competitor_domain; kw_row
            # stores the domain under `source`. Without this mapping the column
            # wrote empty and sheet2 looked like it had no per-domain provenance.
            row["competitor_domain"] = dom
            rows.append(row)
            kept += 1
        per_domain.append({"domain": dom, "keywords": kept,
                           "pulled": got, "offtopic_dropped": got - kept,
                           "total_available": (res or {}).get("total_count")})
    return rows, per_domain


def cross_dedupe(*, sheet1: list[dict], sheet2: list[dict] | None = None,
                 seeds: list[str] | None = None,
                 extra_terms: list[str] | None = None, niche_filter: bool = False
                 ) -> list[dict]:
    """04/05 交叉去重 + 筛选利基 — merge into 4 columns, one row per keyword.

    Keyword arguments only: an earlier ``*sources`` signature silently swallowed
    the seed list as a third source and crashed on str.get(). Keep it explicit.
    """
    seeds = seeds or []
    extra_terms = extra_terms or []
    sources = [sheet1, sheet2 or []]
    merged: dict[str, dict] = {}
    for src in sources:
        for r in src:
            kw = (r.get("keyword") or "").strip()
            if not kw:
                continue
            nk = norm_kw(kw)
            cur = merged.get(nk)
            if cur is None:
                merged[nk] = {"keyword": kw,
                              "intent": r.get("intent") or "",
                              "volume": to_int(r.get("volume")),
                              "difficulty": to_int(r.get("difficulty")),
                              "from": {r.get("source") or "?"}}
            else:
                # Duplicate: keep the larger volume, fill gaps, union sources.
                if to_int(r.get("volume")) > cur["volume"]:
                    cur["volume"] = to_int(r.get("volume"))
                    cur["keyword"] = kw
                if not cur["intent"] and r.get("intent"):
                    cur["intent"] = r["intent"]
                if not cur["difficulty"] and r.get("difficulty"):
                    cur["difficulty"] = to_int(r.get("difficulty"))
                cur["from"].add(r.get("source") or "?")

    out = []
    for row in merged.values():
        if niche_filter and not relevant(row["keyword"], seeds, extra_terms):
            continue
        row["sources"] = "|".join(sorted(row.pop("from")))
        out.append(row)
    out.sort(key=lambda r: (r["volume"], -r["difficulty"]), reverse=True)
    return out


# ------------------------------------------------------------------ writers
SHEET_HEADERS = {
    "sheet1": ["keyword", "intent", "volume", "difficulty"],
    "sheet2": ["keyword", "intent", "volume", "difficulty", "competitor_domain", "position"],
    "sheet3": ["keyword", "intent", "volume", "difficulty", "sources"],
}


def write_csv(path: Path, rows: list[dict], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    log(f"  -> {path} ({len(rows)} rows)")


def write_xlsx(out: Path, sheets: dict[str, tuple[list[dict], list[str]]]) -> Path | None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        log("  (openpyxl not installed — XLSX skipped, CSVs are complete)")
        return None
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(bold=True, color="FFFFFF")
    for name, (rows, headers) in sheets.items():
        ws = wb.create_sheet(name)
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font = head_fill, head_font
            c.alignment = Alignment(horizontal="center", vertical="center")
        for r in rows:
            ws.append([r.get(h) for h in headers])
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        widths = {"keyword": 46, "competitor_domain": 26, "sources": 34,
                  "url": 52, "title": 46, "intent": 9, "volume": 11,
                  "difficulty": 12, "position": 10}
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(h, 14)
    p = out / "fishing_mode_sheets.xlsx"
    wb.save(p)
    log(f"  -> {p} (sheet1 / sheet2 / sheet3)")
    return p


# ------------------------------------------------------------------ offline IO
def read_table(path: Path) -> list[dict]:
    """Read a CSV or XLSX into a list of dicts with lowercased header keys."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.values)
        if not rows:
            return []
        headers = [str(h or "").strip().lower() for h in rows[0]]
        return [dict(zip(headers, r)) for r in rows[1:] if any(r)]
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            with path.open(encoding=enc, newline="") as fh:
                sample = fh.read(4096)
                fh.seek(0)
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                rdr = csv.DictReader(fh, dialect=dialect)
                return [{str(k or "").strip().lower(): v for k, v in row.items()}
                        for row in rdr]
        except (UnicodeDecodeError, csv.Error):
            continue
    return []


COL_ALIASES = {
    "keyword": ["keyword", "keywords", "query", "search term", "关键词", "关键字"],
    "volume": ["volume", "search volume", "vol", "vol.", "sv", "搜索量", "月搜索量"],
    "difficulty": ["difficulty", "kd", "kd%", "kd %", "keyword difficulty",
                   "competition", "竞争难度", "难度"],
    "intent": ["intent", "search intent", "搜索意图", "意图"],
    "url": ["url", "link", "result url", "地址", "链接"],
    "domain": ["domain", "网站", "域名"],
    "position": ["position", "rank", "rank_group", "pos", "排名", "位置"],
}


def pick(row: dict, field: str):
    for alias in COL_ALIASES[field]:
        if alias in row and str(row[alias]).strip() not in ("", "None"):
            return row[alias]
    return None


def offline_rows(path: Path, source: str, keyword_fallback: str | None = None) -> list[dict]:
    """Normalise a hand-exported table into internal rows.

    `keyword_fallback` matters for SERP exports (SEO Minion, DataForSEO SERP
    sheets): those files carry position/url/title but no keyword column, because
    each file is the result for one query. Refusing rows without a keyword column
    silently yielded zero hits — the whole offline SERP path looked like it worked
    while producing nothing.
    """
    out = []
    for row in read_table(path):
        k = pick(row, "keyword") or keyword_fallback
        if not k:
            continue
        out.append({
            "keyword": str(k).strip(),
            "intent": norm_intent(pick(row, "intent")),
            "volume": to_int(pick(row, "volume")),
            "difficulty": to_int(pick(row, "difficulty")),
            "source": source,
            "url": pick(row, "url") or "",
            "domain": bare(str(pick(row, "domain") or pick(row, "url") or "")),
            "position": to_int(pick(row, "position")) or None,
        })
    return out


def keyword_from_filename(path: Path) -> str:
    """'serp_lithium forklift battery.csv' -> 'lithium forklift battery'."""
    stem = path.stem
    for prefix in ("serp_", "serp-", "sheet_"):
        if stem.lower().startswith(prefix):
            stem = stem[len(prefix):]
    return stem.replace("_", " ").strip()


def offline_serp_dir(paths: list[str], exclude: set[str] | None = None
                     ) -> tuple[list[dict], list[dict]]:
    """Read SEO Minion (or any) SERP exports: one file per keyword, or one big file.

    Keyword resolution order: a `keyword` column if present (big combined export),
    otherwise the filename, otherwise the stem. Junk domains are dropped here so
    the offline path applies the same filter as the API path.
    """
    exclude = exclude or set()
    hits, dropped = [], []
    for pattern in paths:
        matches = sorted(glob.glob(pattern))
        if not matches:
            log(f"  (no files matched {pattern})")
        for f in matches:
            p = Path(f)
            fallback = keyword_from_filename(p)
            for r in offline_rows(p, "serp", keyword_fallback=fallback):
                domain = base_domain(r["domain"]) if r["domain"] else ""
                if not domain:
                    continue
                hit = {"keyword": r["keyword"], "position": r["position"],
                       "domain": domain, "url": r["url"], "title": ""}
                reason = is_junk(domain, exclude)
                if reason:
                    dropped.append(hit | {"reason": reason})
                else:
                    hits.append(hit)
    return hits, dropped


# ------------------------------------------------------------------ driver
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="捕鱼法 keyword research — SEO小平 Fishing-Mode SOP, automated.")
    ap.add_argument("--seeds", nargs="+", default=[], help="5 seed keywords for the product")
    ap.add_argument("--seeds-file", help="file with one seed keyword per line")
    ap.add_argument("--context", nargs="*", default=[],
                    help="extra terms that count as 'on-topic' (e.g. forklift lipo)")
    ap.add_argument("--mode", choices=["api", "offline"], default="api")
    ap.add_argument("--location", type=int, default=2840, help="DataForSEO location_code (2840=US)")
    ap.add_argument("--language", default="en")
    ap.add_argument("--sheet1-keep", type=int, default=SHEET1_KEEP)
    ap.add_argument("--serp-targets", type=int, default=20,
                    help="how many sheet1 keywords to pull SERPs for (cost control)")
    ap.add_argument("--serp-depth", type=int, default=SERP_DEPTH)
    ap.add_argument("--domain-kw-limit", type=int, default=DOMAIN_KW_LIMIT)
    ap.add_argument("--max-domains", type=int, default=15,
                    help="cap domains sent to step 3 (cost control)")
    ap.add_argument("--extra-exclude", nargs="*", default=[], help="extra junk domains")
    ap.add_argument("--offline", nargs="*", default=[],
                    help="offline inputs as key=path, key in {ideas,serp,domains}")
    ap.add_argument("--niche-filter", action="store_true",
                    help="also drop off-topic keywords from sheet3 (step 05)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--dry-run", action="store_true", help="estimate cost only")
    args = ap.parse_args(argv)

    seeds = list(args.seeds)
    if args.seeds_file:
        seeds += [l.strip() for l in Path(args.seeds_file).read_text(encoding="utf-8").splitlines()
                  if l.strip()]
    seeds = list(dict.fromkeys(seeds))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if not seeds and args.mode == "api":
        log("ERROR: pass --seeds (5 seed keywords, per step 01) or --seeds-file.")
        return 2
    log(f"seeds ({len(seeds)}): {', '.join(seeds) or '(from offline exports)'}")

    exclude = {base_domain(bare(d)) for d in args.extra_exclude}
    dfs = DataForSEO()

    ideas, hits, domains, dom_rows = [], [], [], []
    dropped_all: list[dict] = []

    if args.mode == "api":
        if not dfs.available:
            log("ERROR: DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD unset.")
            log("       Export them, or run --mode offline with your own exports.")
            return 2
        est = 0.05 + 0.005 * args.serp_targets + 0.05 * args.max_domains
        log(f"estimated API cost: ~${est:.2f} "
            f"(ideas 0.05 + {args.serp_targets} SERPs x 0.005 + {args.max_domains} domains x 0.05)")
        if args.dry_run:
            return 0

        log("\n[01 以词找词] expanding seeds into keyword ideas")
        ideas = step1_ideas(dfs, seeds, args.location, args.language)
        write_csv(out / "step1_ideas.csv", ideas, SHEET_HEADERS["sheet1"])

        sheet1 = step2_sheet1(ideas, seeds, args.context, args.sheet1_keep)
        log(f"\n[02 用词找站] sheet1 = {len(sheet1)} related high-traffic keywords")
        write_csv(out / "sheet1.csv", sheet1, SHEET_HEADERS["sheet1"])

        targets = [r["keyword"] for r in sheet1][: args.serp_targets]
        log(f"  pulling top-{args.serp_depth} organic for {len(targets)} keywords")
        hits, dropped = serp_top_domains(dfs, targets, args.location, args.language,
                                         args.serp_depth, exclude)
        write_csv(out / "step2_serp_hits.csv", hits,
                  ["keyword", "position", "domain", "url", "title"])
        write_csv(out / "step2_serp_dropped.csv", dropped,
                  ["keyword", "position", "domain", "url", "title", "reason"])
        domains = collect_domains(hits)
        write_csv(out / "step2_domains.csv", domains,
                  ["domain", "n_keywords", "hits", "best"])
        log(f"  {len(domains)} competitor domains survive the junk filter")

        chosen = [d["domain"] for d in domains if looks_like_domain(d["domain"])][: args.max_domains]
        if not chosen:
            log("  ! no valid competitor domains survived; sheet2 and sheet3 will be seed-only")
        log(f"\n[03 用站找词] top-{args.domain_kw_limit} ranked keywords x {len(chosen)} domains")
        dom_rows, per_domain = step3_domain_keywords(dfs, chosen, args.location,
                                                     args.language, args.domain_kw_limit,
                                                     seeds, args.context)
        write_csv(out / "sheet2.csv", dom_rows, SHEET_HEADERS["sheet2"])
        write_csv(out / "step3_domain_summary.csv", per_domain,
                  ["domain", "keywords", "pulled", "offtopic_dropped", "total_available"])
        dfs.report()

    else:  # offline
        for spec in args.offline:
            if "=" not in spec:
                log(f"  (skip {spec}: expected key=path)")
                continue
            key, _, val = spec.partition("=")
            if key == "ideas":
                ideas += offline_rows(Path(val), "ideas")
            elif key == "serp":
                h, d = offline_serp_dir([val], exclude)
                hits += h
                dropped_all += d
            elif key == "domains":
                raw_dom = offline_rows(Path(val), "manual")
                # Same off-topic filter as the API path, so both modes agree.
                for r in raw_dom:
                    if seeds and not relevant(r["keyword"], seeds, args.context):
                        continue
                    dom_rows.append(r)
            else:
                log(f"  (unknown offline key '{key}')")
        if not hits:
            for p in glob.glob(str(out / "*serp*.csv")):
                h, d = offline_serp_dir([p], exclude)
                hits += h
                dropped_all += d
        if not ideas and not dom_rows and not hits:
            log("ERROR: no offline inputs found. Use --offline ideas=... serp=... domains=...")
            return 2
        if ideas:
            write_csv(out / "step1_ideas.csv", ideas, SHEET_HEADERS["sheet1"])
            sheet1 = step2_sheet1(ideas, seeds, args.context, args.sheet1_keep)
            write_csv(out / "sheet1.csv", sheet1, SHEET_HEADERS["sheet1"])
        if hits:
            write_csv(out / "step2_serp_hits.csv", hits,
                      ["keyword", "position", "domain", "url", "title"])
            write_csv(out / "step2_serp_dropped.csv", dropped_all,
                      ["keyword", "position", "domain", "url", "title", "reason"])
            log(f"  {len(hits)} organic hits kept, {len(dropped_all)} junk results dropped")
            domains = collect_domains(hits)
            write_csv(out / "step2_domains.csv", domains,
                      ["domain", "n_keywords", "hits", "best"])
        if dom_rows:
            write_csv(out / "sheet2.csv", dom_rows, SHEET_HEADERS["sheet2"])

    # --- 04 交叉去重 -> sheet3
    log("\n[04 交叉去重] merging sheet1 + sheet2 -> sheet3 (4 params kept)")
    sheet1_rows = []
    if (out / "sheet1.csv").exists():
        sheet1_rows = [{**r, "volume": to_int(r.get("volume")),
                        "difficulty": to_int(r.get("difficulty")), "source": "sheet1"}
                       for r in read_table(out / "sheet1.csv")]
    sheet2_rows = [{**r, "volume": to_int(r.get("volume")),
                    "difficulty": to_int(r.get("difficulty")), "source": r.get("competitor_domain") or "sheet2"}
                   for r in read_table(out / "sheet2.csv")] if (out / "sheet2.csv").exists() else []

    sheet3 = cross_dedupe(sheet1=sheet1_rows, sheet2=sheet2_rows, seeds=seeds,
                          extra_terms=args.context, niche_filter=args.niche_filter)
    write_csv(out / "sheet3.csv", sheet3, SHEET_HEADERS["sheet3"])
    write_xlsx(out, {
        "sheet1": (sheet1_rows, SHEET_HEADERS["sheet1"]),
        "sheet2": (sheet2_rows, SHEET_HEADERS["sheet2"]),
        "sheet3": (sheet3, SHEET_HEADERS["sheet3"]),
    })
    log(f"\n[done] sheet3: {len(sheet3)} unique keywords "
        f"(from {len(sheet1_rows)} sheet1 + {len(sheet2_rows)} sheet2 rows)")
    return 0


def collect_domains(hits: list[dict]) -> list[dict]:
    """Aggregate SERP hits per domain: breadth (how many of your keywords show it),
    best position, and hit count. Breadth-first ordering is the SOP's intent —
    sites appearing across many of your keywords are the real topical competitors.
    """
    agg: dict[str, dict] = {}
    for h in hits:
        a = agg.setdefault(h["domain"], {"domain": h["domain"], "hits": 0,
                                         "best": 99, "kws": set()})
        a["hits"] += 1
        a["best"] = min(a["best"], h.get("position") or 99)
        a["kws"].add(h["keyword"])
    rows = [{"domain": a["domain"], "n_keywords": len(a["kws"]),
             "hits": a["hits"], "best": a["best"],
             "keywords": sorted(a["kws"])} for a in agg.values()]
    rows.sort(key=lambda d: (-d["n_keywords"], d["best"]))
    return rows


try:
    import requests  # noqa: E402
except ImportError:
    requests = None  # offline mode still works; API mode reports missing dep


if __name__ == "__main__":
    sys.exit(main())
