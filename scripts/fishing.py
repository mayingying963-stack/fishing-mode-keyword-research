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
PER_SEED_IDEAS = 30       # step 1: candidate keywords PER seed (5 seeds -> 150)
SHEET1_KEEP = 30          # per-seed cap applied inside sheet1 (kept for API compat)
SERP_DEPTH = 50           # step 3: top-50 organic results per keyword
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

    # Service/training/rental phrases are never the product, in any vertical:
    # "inverter certified", "contactor rental", "battery repair near me".
    if NON_PRODUCT_INTENT & kset or matched_phrase(k, NON_PRODUCT_INTENT):
        return False

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
    # The trailing word of most seeds is the category noun ("battery", "inverter",
    # "contactor"). Compute it first: when every seed shares a single industry word,
    # `required` and `category` collapse into the same token and the rule degrades
    # to "contains the category ⇒ pass", which let "ac contactor" through a DC
    # contactor seed set and "pure sine wave inverter" through a solar one.
    tail: dict[str, int] = {}
    for toks in seed_tok_lists:
        tail[toks[-1]] = tail.get(toks[-1], 0) + 1
    category = {w for w, c in tail.items() if c >= max(2, (len(seed_tok_lists) + 1) // 2)}
    if not category:
        category = set.intersection(*map(set, seed_tok_lists)) or set(seed_tok_lists[0][-1:])

    # Required = industry words shared by all seeds, EXCLUDING the category noun.
    # "forklift battery" seeds → required {"forklift"}; "dc contactor" seeds where
    # every seed is <modifier> + "contactor" → required = the non-category shared
    # words (here {"dc"}). If that leaves nothing, the seeds carry no industry
    # qualifier at all and the category alone is the only signal available.
    required = {w for w, c in freq.items()
                if c == len(seed_tok_lists) and len(w) > 2 and w not in category}

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

    # A keyword made of nothing but category words ("contactor", "inverter") is a
    # different, broader product: it passes only if it is literally one of the
    # seeds. Without this, a "dc contactor" seed set accepts "contactor" and
    # "ac contactor", which are the AC product line.
    content = kset - STOP
    if category and content <= category:
        seed_sets = [set(re.findall(r"[a-z0-9]+", s.lower())) - STOP for s in seeds]
        if not any(ss == content for ss in seed_sets):
            return False
    # The competing product line is not a variant: "ac contactor" for a DC product.
    if opposites_of(seeds) & kset:
        return False
    # An adjacent vertical is a different product: "car battery" for a forklift
    # battery business. Skipped when a seed names that vertical (a golf-cart
    # battery seller keeps "golf").
    seed_tokens = {w for s in seeds for w in re.findall(r"[a-z0-9]+", s.lower())}
    if (ADJACENT_VERTICALS & kset) - seed_tokens:
        return False
    return True


def seed_category_tokens(seeds: list[str]) -> set[str]:
    """The category-bearing tokens of a seed set: trailing noun(s) + every seed token.

    Used by the relaxed competitor rule to decide what counts as "same product
    family". The full seed vocabulary is included on purpose — a lithium battery
    rival's list is mostly "lithium battery", which shares only the category with
    a forklift-battery seed.
    """
    toks: set[str] = set()
    tails: set[str] = set()
    for s in seeds:
        st = [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1]
        if not st:
            continue
        tails.add(st[-1])
        toks |= set(st)
    return toks | tails


def matched_phrase(keyword: str, phrases: set[str]) -> bool:
    """True when any phrase occurs in the keyword as a whole-word run.

    Single tokens are matched on token boundaries; multi-word entries ("repair
    near", "service near") are matched as substrings so they can span tokens.
    """
    if not phrases:
        return False
    k = f" {' '.join(re.findall(r'[a-z0-9]+', (keyword or '').lower()))} "
    for p in phrases:
        p = p.strip().lower()
        if not p:
            continue
        if " " in p:
            if f" {p} " in k:
                return True
        elif f" {p} " in k:
            return True
    return False


# Verticals that are a different product even when the category word appears.
# Keep this list to truly cross-industry nouns. Deliberately absent:
#   "truck"  — a forklift IS a "forklift truck"; listing it rejected
#              "battery for forklift truck" and "forklift truck battery chargers"
#   "home"/"residential" — ordinary modifiers for solar and storage products
#   "solar"  — only adjacent when the product is not itself solar
ADJACENT_VERTICALS = {
    "car", "auto", "automotive", "tractor", "marine", "boat", "rv",
    "camper", "motorcycle", "scooter", "ebike", "bike", "phone", "laptop",
    "golf", "cart", "ups", "generator", "drone", "powerwall",
    "hearing", "watch", "toys",
}

# Keywords that are about the *machine* or the *service*, never the part we sell.
# A competitor selling batteries also ranks for "forklift certified" (a course),
# "forklift rental near me" (a service) and "forklift repair" — dropping these is
# the difference between a battery keyword list and a forklift-industry dump.
NON_PRODUCT_INTENT = {
    "certified", "certification", "training", "course", "classes", "license",
    "licence", "rental", "rent", "rentals", "hire", "leasing", "lease",
    "operator", "job", "jobs", "salary", "career", "school", "test", "exam",
    "auction", "wiring diagram",
}

# Conventional opposites. If a seed says "dc", the keyword saying "ac" is the
# competing product line, not a variant — same for on/off-grid, indoor/outdoor.
ANTONYM_GROUPS = [
    {"dc", "ac"},
    {"on", "off"},
    {"indoor", "outdoor"},
    {"wired", "wireless"},
    {"manual", "automatic", "auto"},
    {"single", "three", "3"},
    {"lithium", "lead"},
]


def opposites_of(seeds: list[str]) -> set[str]:
    """Tokens that name the competing product line of a seed token.

    Seeds mentioning "dc" ⇒ pairs with "ac". Seeds mentioning "lithium" ⇒ pairs
    with "lead". Used to reject "ac contactor" for a "dc contactor" product
    without hardcoding every vertical's vocabulary.
    """
    seed_tokens = {w for s in seeds for w in re.findall(r"[a-z0-9]+", s.lower())}
    out: set[str] = set()
    for group in ANTONYM_GROUPS:
        if group & seed_tokens:
            out |= group - seed_tokens
    return out


def relevant_competitor(keyword: str, seeds: list[str], extra_terms: list[str]) -> bool:
    """Step 03 relevance: relaxed rule for keywords a *competitor domain* ranks for.

    The strict rule in `relevant()` demands the industry qualifier ("forklift").
    Applied to competitor keywords it was catastrophic on real data: leochlithium.us,
    redwaypower.com, malinusa.com and innovdepot.com — all battery rivals surfaced
    by our own SERPs — kept **0 of 100** keywords each, because their lists read
    "lithium battery", "lifepo4 battery", "48v lithium battery". Those keywords are
    the category itself, not a different product.

    So for step 03 the test is the inverse: keep anything carrying a category token
    unless it names an adjacent vertical (solar, golf cart, car, marine, RV...).
    A domain only reaches step 03 because it ranked for our keywords, which is
    already strong evidence it is in the same business.
    """
    k = (keyword or "").lower()
    if not k:
        return False
    kset = set(re.findall(r"[a-z0-9]+", k))
    # Service/training/rental words are never the product, whatever else is in
    # the phrase: "forklift certified", "forklift rental near me", "forklift jobs".
    # Matched as phrases so multi-word entries like "repair near" work too.
    if NON_PRODUCT_INTENT & kset or matched_phrase(k, NON_PRODUCT_INTENT):
        return False
    keep_tokens = seed_category_tokens(seeds)
    if extra_terms:
        for t in extra_terms:
            keep_tokens |= set(re.findall(r"[a-z0-9]+", str(t).lower()))
    if not (keep_tokens & kset):
        return False
    # The bare category noun alone is not enough: for "dc contactor" seeds the
    # keyword "contactor" (or "ac contactor") is a different product. Require at
    # least one *modifier* token shared with a seed, unless the keyword is itself
    # a seed phrase. Modifiers = seed tokens minus the trailing category noun.
    tails: set[str] = set()
    for s in seeds:
        st = [w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1]
        if st:
            tails.add(st[-1])
    modifiers = {w for s in seeds for w in re.findall(r"[a-z0-9]+", s.lower())} - tails
    if modifiers:
        # A category-only keyword ("battery", "module") passes only when it IS one
        # of our seeds; otherwise it needs a modifier from a seed.
        seed_phrases = [set(re.findall(r"[a-z0-9]+", s.lower())) for s in seeds]
        is_seed = any(sp == kset for sp in seed_phrases)
        if not is_seed and not (modifiers & kset):
            return False
    # An adjacent vertical disqualifies, unless one of our seeds names it too
    # (a business that genuinely sells golf-cart batteries passes "golf").
    seed_tokens = {w for s in seeds for w in re.findall(r"[a-z0-9]+", s.lower())}
    blocked = (ADJACENT_VERTICALS & kset) - seed_tokens
    if blocked:
        return False
    # A different head noun means a different product: "solar panel" and
    # "golf club" borrow the seed's modifier but are not the thing we sell. Pass
    # only if the keyword's own head noun is a seed category word, or the keyword
    # is a seed phrase.
    seed_sets = [set(re.findall(r"[a-z0-9]+", s.lower())) - {"for", "of", "in", "the",
                                                            "a", "an", "to", "and"}
                 for s in seeds]
    if kset and any(ss <= kset for ss in seed_sets):
        return True
    heads = {next(iter(re.findall(r"[a-z0-9]+", s.lower())[-1:]), "") for s in seeds}
    content = {w for w in kset if len(w) > 2}
    if heads and not (heads & content):
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
    """Flatten a DataForSEO keyword item into the SOP's 4 params.

    Two different response shapes come through here, and mixing them up silently
    zeroed every difficulty value:

      * ``keyword_suggestions`` / ``keyword_ideas`` return the keyword data
        FLAT at the top level: ``item["keyword"]``, ``item["keyword_info"]``,
        ``item["keyword_properties"]["keyword_difficulty"]``.
      * ``ranked_keywords`` nests it under ``item["keyword_data"]``...

    So look for the nested payload first and fall back to the item itself, then
    read each sub-object from wherever it actually lives.
    """
    kd = item.get("keyword_data") or item
    # keyword_info / keyword_properties / search_intent_info may sit on either level.
    info = kd.get("keyword_info") or item.get("keyword_info") or {}
    props = kd.get("keyword_properties") or item.get("keyword_properties") or {}
    intent_info = (kd.get("search_intent_info")
                   or item.get("search_intent_info") or {})
    return {
        "keyword": (kd.get("keyword") or item.get("keyword") or "").strip(),
        "intent": norm_intent(intent_info.get("main_intent")),
        "volume": to_int(info.get("search_volume")),
        "difficulty": to_int(props.get("keyword_difficulty")),
        "source": source,
    }


# ------------------------------------------------------------------ steps
def step1_ideas(dfs: DataForSEO, seeds: list[str], loc: int, lang: str,
                per_seed: int = PER_SEED_IDEAS,
                use_ideas: bool = True) -> list[dict]:
    """01 以词找词 — expand EACH seed into `per_seed` candidate keywords.

    The SOP quantifies this step: 5 seeds x 30 candidates = 150 sheet1 rows.
    So each seed gets its own budget; a blended pool would let one high-volume
    seed starve the rest.

    `keyword_suggestions` is the primary source per seed: it stays on-topic and
    returns real volume for the seed itself. `keyword_ideas` is an associative
    broaden and drifts badly (seeding "forklift battery" returned smoke-detector
    and car-battery terms with 200k volume), so it only fills seeds that
    suggestions could not bring up to `per_seed`, and its rows still pass the
    relevance filter in step 2.
    """
    rows: list[dict] = []
    seen: set[str] = set()

    def take(items: list[dict], source: str, want: int) -> int:
        """Append up to `want` new, on-topic-ish rows. Returns how many were added."""
        added = 0
        for it in items:
            if added >= want:
                break
            row = kw_row(it, source)
            nk = norm_kw(row["keyword"])
            if not row["keyword"] or nk in seen:
                continue
            seen.add(nk)
            rows.append(row)
            added += 1
        return added

    def one(seed: str):
        res = dfs.post("dataforseo_labs/google/keyword_suggestions/live", {
            "keyword": seed, "location_code": loc, "language_code": lang,
            "limit": max(per_seed * 2, 100), "include_seed_keyword": True,
        })
        return seed, res

    with ThreadPoolExecutor(max_workers=5) as pool:
        per_seed_results = list(pool.map(one, seeds))

    for seed, res in per_seed_results:
        got = take((res or {}).get("items") or [], f"suggest:{seed}", per_seed)
        log(f"  seed '{seed}' -> {got}/{per_seed} candidates from suggestions")

    # Top up any seed that came up short, one targeted call per weak seed.
    short = [s for s, res in per_seed_results]
    for seed in short:
        have = sum(1 for r in rows if r["source"] == f"suggest:{seed}") + \
               sum(1 for r in rows if r["source"] == f"ideas:{seed}")
        if have >= per_seed or not use_ideas:
            continue
        try:
            res = dfs.post("dataforseo_labs/google/keyword_ideas/live", {
                "keywords": [seed], "location_code": loc, "language_code": lang,
                "limit": (per_seed - have) + 20, "include_serp_info": False,
            })
        except RuntimeError as err:
            # A top-up is best-effort: one seed staying short must not kill the run.
            log(f"  ! ideas top-up failed for '{seed}': {err}")
            continue
        extra = take((res or {}).get("items") or [], f"ideas:{seed}", per_seed - have)
        if extra:
            log(f"  seed '{seed}' topped up with {extra} idea rows")

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
    """02 用词找站 (part 1) — per-seed top-`keep` keywords -> sheet1.

    The SOP gives every seed its own net: 5 seeds x 30 = up to 150 rows. Each
    seed must contribute its own best `keep` keywords, otherwise the loudest
    seed takes the whole table and the other four get no representation.

    Grouping: rows built by step1 carry `source` = "suggest:<seed>" or
    "ideas:<seed>". Rows from other paths (offline exports, a re-read of
    sheet1.csv) have no such prefix, so they are attributed to the seed they
    overlap most. Previously every un-prefixed row landed in one bucket named
    after its raw source, which silently collapsed the whole table to `keep`
    rows total instead of `keep` per seed.
    """
    filtered = [r for r in ideas if relevant(r["keyword"], seeds, extra_terms)]

    def seed_of(row: dict) -> str:
        src = row.get("source") or ""
        if ":" in src:
            candidate = src.split(":", 1)[1]
            if candidate in seeds:
                return candidate
        # Fall back to the seed sharing the most tokens with the keyword.
        ktok = set(re.findall(r"[a-z0-9]+", (row.get("keyword") or "").lower()))
        best, best_score = (seeds[0] if seeds else "seed"), -1
        for s in seeds:
            stok = set(re.findall(r"[a-z0-9]+", s.lower()))
            score = len(ktok & stok)
            if score > best_score:
                best, best_score = s, score
        return best

    by_seed: dict[str, list[dict]] = {}
    for r in filtered:
        by_seed.setdefault(seed_of(r), []).append(r)

    out: list[dict] = []
    for seed, seed_rows in by_seed.items():
        seed_rows.sort(key=lambda r: (r["volume"], -(r["difficulty"] or 0)), reverse=True)
        picked = seed_rows[:keep]
        log(f"  seed {seed!r}: {len(picked)} of {len(seed_rows)} on-topic rows -> sheet1")
        out.extend(picked)
    out.sort(key=lambda r: (r["volume"], -(r["difficulty"] or 0)), reverse=True)
    return out


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

    return hits, dropped


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
            if seeds and not relevant_competitor(row["keyword"], seeds, extra_terms):
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
    # Internal trace file: keeps which seed/domain produced each row so reruns and
    # offline merges can regroup rows instead of guessing from the keyword text.
    "trace": ["keyword", "intent", "volume", "difficulty", "source"],
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
    ap.add_argument("--per-seed-ideas", type=int, default=PER_SEED_IDEAS,
                    help="step 01 candidates per seed (5 seeds x 30 = 150 sheet1 rows)")
    ap.add_argument("--sheet1-keep", type=int, default=SHEET1_KEEP,
                    help="per-seed cap inside sheet1 (30 => 5*30=150 rows)")
    ap.add_argument("--serp-targets", type=int, default=0,
                    help="0 = run SERPs for EVERY sheet1 keyword (SOP behaviour). "
                         "Set a number only to cap cost on a trial run.")
    ap.add_argument("--serp-depth", type=int, default=SERP_DEPTH)
    ap.add_argument("--domain-kw-limit", type=int, default=DOMAIN_KW_LIMIT)
    ap.add_argument("--max-domains", type=int, default=0,
                    help="0 = step 03 for EVERY surviving domain (SOP behaviour). "
                         "Set a number only to cap cost on a trial run.")
    ap.add_argument("--min-breadth", type=int, default=2,
                    help="only mine domains appearing for at least N of your keywords. "
                         "1 keeps the long tail (hundreds of one-off domains, mostly noise "
                         "and a large bill); 3 focuses on real topical competitors.")
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
        # SOP volume chain: 5 seeds -> 5*30 candidates -> 5*30 SERPs -> every
        # surviving domain. The domain count is not known before step 02 runs, so
        # when no cap is set the estimate must say so instead of pricing an
        # arbitrary 30 domains and understating the bill by 10x.
        n_seed = max(1, len(seeds))
        n_sheet1 = n_seed * args.sheet1_keep
        n_serp = args.serp_targets or n_sheet1
        base = 0.05 * n_seed + 0.005 * n_serp
        if args.max_domains:
            est = base + 0.05 * args.max_domains
            log(f"estimated API cost: ~${est:.2f} "
                f"(step01 {n_seed}x$0.05 + step02 {n_serp}x$0.005 + "
                f"step03 {args.max_domains} domains x$0.05)")
        else:
            # Up to ~50 organic results per SERP, minus platforms: expect a few
            # hundred distinct domains, i.e. one $0.05 call each.
            lo = base + 0.05 * (n_serp * 5)
            hi = base + 0.05 * (n_serp * 12)
            log(f"estimated API cost: ~${lo:.2f}-${hi:.2f} "
                f"(step01 {n_seed}x$0.05 + step02 {n_serp}x$0.005 + "
                f"EVERY surviving domain x$0.05 — unbounded until step 02 finishes)")
            log("  tip: pass --max-domains N to cap step 03, or --dry-run to stop here")
        if args.dry_run:
            return 0

        log("\n[01 以词找词] expanding each seed into candidates")
        ideas = step1_ideas(dfs, seeds, args.location, args.language, args.per_seed_ideas)
        write_csv(out / "step1_ideas.csv", ideas, SHEET_HEADERS["trace"])

        sheet1 = step2_sheet1(ideas, seeds, args.context, args.sheet1_keep)
        log(f"\n[02 用词找站] sheet1 = {len(sheet1)} related high-traffic keywords "
            f"(cap {args.sheet1_keep}/seed)")
        write_csv(out / "sheet1.csv", sheet1, SHEET_HEADERS["sheet1"])

        targets = [r["keyword"] for r in sheet1]
        if args.serp_targets:
            targets = targets[: args.serp_targets]
            log(f"  (cost cap: only {len(targets)} of {len(sheet1)} keywords)")
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
        log(f"  {len(domains)} unique domains survive the junk filter "
            f"({len(hits)} hits kept / {len(dropped)} dropped)")

        # max_domains=0 means "every surviving domain" (SOP behaviour); slicing
        # with [:0] would silently drop them all, so treat 0 as no cap.
        # min_breadth drops the long tail: domains that showed up for only one of
        # your keywords are mostly unrelated sites that happened to rank once, and
        # at $0.05 each they are the bulk of the bill for very little signal.
        chosen = [d["domain"] for d in domains
                  if looks_like_domain(d["domain"])
                  and d["n_keywords"] >= max(1, args.min_breadth)]
        if args.max_domains:
            chosen = chosen[: args.max_domains]
        log(f"  breadth filter >= {args.min_breadth} keywords: "
            f"{len(chosen)} of {len(domains)} domains kept for step 03")
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
                    if seeds and not relevant_competitor(r["keyword"], seeds, args.context):
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
            write_csv(out / "step1_ideas.csv", ideas, SHEET_HEADERS["trace"])
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
