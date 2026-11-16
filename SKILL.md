---
name: fishing-mode-keyword-research
description: >-
  Use when the user wants a full keyword universe for one product built from
  scratch — the "捕鱼法 / Fishing-Mode" SOP (SEO小平). Five seed keywords →
  keyword-ideas expansion → top-50 Google SERP per keyword → junk-domain
  filtering → competitor domain keyword mining → cross-deduped
  sheet1/sheet2/sheet3 with keyword, intent, volume, difficulty. Triggers on
  "捕鱼法", "种子词", "以词找词", "用词找站", "关键词词库", "competitor keyword
  mining", "build a keyword universe", "SEO小平". Runs against DataForSEO API or
  your own SEO Minion / Semrush / Ahrefs / DataForSEO exports.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [seo, keyword-research, dataforseo, serp, competitor-analysis, b2b]
    related_skills: [keyword-research, seo-dataforseo, dataforseo-tool, serpapi-tool]
---

# 捕鱼法 / Fishing-Mode Keyword Research

## Overview

捕鱼模式 treats every keyword as one net: cast more nets, catch more traffic. The
method is a five-step funnel that ends with a single deduplicated table of
keywords you can actually rank for.

```
01 以词找词   5 seed keywords → keyword-ideas expansion
02 用词找站   top-volume on-topic keywords → Google top-50 organic → drop junk domains
03 用站找词   each surviving domain → its top-100 ranked keywords → sheet2
04 交叉去重   merge 01 + 03 → one row per keyword, 4 params → sheet3
05 筛选利基   filter sheet3 to the niche worth attacking
```

The script does all five steps and writes every intermediate file, so a failed
or expensive step never costs you the work before it.

**The one rule that decides output quality:** steps 02 and 03 both need to know
what "related to my product" means. Sharing the category noun is not enough —
`car battery` and `solar battery` both contain *battery*, and a forklift-battery
competitor will happily rank for both. The script derives the industry qualifier
from tokens present in **every** seed (`forklift`) and treats the trailing noun
as the category (`battery`); a keyword must carry both. Seed your 5 keywords so
that at least one token truly appears in all of them.

## When to Use

- Building a keyword universe for one product from a blank page
- User says 捕鱼法, 以词找词, 用词找站, 种子词, 关键词词库
- You need competitor keyword mining that isn't just "export the domain's keywords"
- A delimited, de-duplicated sheet is the deliverable rather than a narrative report

**Don't use for:** auditing an existing site's rankings (use `seo-audit`), a single
keyword's difficulty check (use `seo-dataforseo`), or local-pack/grid work
(use `seo-maps`).

## Setup

```bash
pip install requests openpyxl          # requests only needed for --mode api

export DATAFORSEO_LOGIN=you@example.com
export DATAFORSEO_PASSWORD=...          # DataForSEO API password, NOT your site login
```

Without credentials, run `--mode offline` against exports you already have. Both
paths produce the same three sheets.

**Before trusting this skill, run the self-test.** It needs no API key, no
network, and costs nothing.

```bash
python scripts/selftest.py     # expect "N passed, 0 failed"
```

It asserts the relevance matrix (both directions), junk-domain filtering,
plural/separator dedup, and a full offline run. If anything fails, fix that
before running a paid job — a broken relevance rule silently pollutes sheet3
while every file still gets written and every row count still looks plausible.

See `references/relevance-filter-iterations.md` for the four broken
implementations this rule went through. Read it before "improving" the filter,
because the two obvious deltas are traps that produce wrong output rather than
errors.

## The Five Steps

### 01 以词找词 — seeds to candidates

Pick **5** seed keywords (the original SOP says 5–10; 5 keeps cost predictable).
Seed choice controls everything downstream, so follow these:

- express the product, not the brand: `forklift battery`, not `BlitzNXT battery`
- include the material/chemistry variant your buyers search: `lithium forklift battery`
- include a voltage or spec variant: `48v forklift battery`
- include a buying-intent variant: `forklift battery replacement`
- keep one **shared industry token** across all five (`forklift`)

The script calls `dataforseo_labs/google/keyword_ideas/live` with all seeds at once
(one API call) and writes `step1_ideas.csv`.

*Done when:* `step1_ideas.csv` has at least a few hundred candidate rows and every
seed itself appears in it.

### 02 用词找站 — candidates to SERP domains

Two sub-steps from the original SOP, in one command:

1. keep the top **30** on-topic, highest-volume candidates → `sheet1.csv`
2. pull `serp/google/organic/live/advanced` at depth 50 for the top-N of those
   (`--serp-targets`, default 20) and drop junk domains

Junk filtering is what stops the whole flow from collapsing into a marketplace
export. `amazon.com`, `walmart.com`, `youtube.com` and friends are removed by
default (`JUNK_DOMAINS`), and every drop is written to
`step2_serp_dropped.csv` with a `reason` so you can audit the decision.

*Done when:* `step2_domains.csv` lists real brand/retailer domains, and
`step2_serp_dropped.csv` accounts for every excluded result.

### 03 用站找词 — domains to sheet2

For each surviving domain (ranked by how many of your keywords it appears for,
then by best position), pull `dataforseo_labs/google/ranked_keywords/live` at
limit 100 and keep only on-topic keywords → `sheet2.csv`.

The off-topic filter runs here too. A forklift-battery competitor usually also
ranks for solar and golf-cart batteries; without this filter those leak into the
final table (observed, not theoretical).

*Done when:* `step3_domain_summary.csv` shows per-domain `pulled` vs
`offtopic_dropped`, and no off-topic keyword survives in `sheet2.csv`.

### 04 交叉去重 — sheet3

Merge sheet1 + sheet2 into **one row per keyword**, keeping exactly the four
params the SOP names — keyword, intent, volume, difficulty — plus a `sources`
column so you can see whether a term came from seed expansion, competitor
mining, or both. Duplicates keep the larger volume, fill intent/difficulty gaps,
and union their sources.

Dedup normalises singular/plural and separator differences (`48v-forklift-battery`
== `48v forklift batteries`).

*Done when:* `sheet3.csv` has no duplicate keyword under normalisation and the
row count is ≤ sheet1 + sheet2.

### 05 筛选利基 — optional narrowing

`--niche-filter` re-applies the relevance rule at the sheet3 stage. Usually
redundant (steps 02/03 already filtered); reach for it when you widened with
`--context` and want a stricter final cut.

## Command Recipes

Automated, forklift-battery example:

```bash
python scripts/fishing.py \
  --seeds "forklift battery" "lithium forklift battery" "forklift battery charger" \
          "48v forklift battery" "forklift battery replacement" \
  --location 2840 --language en \
  --serp-targets 20 --max-domains 15 \
  --out out/
```

Check cost before spending — `--dry-run` prints the estimate and exits:

```bash
python scripts/fishing.py --seeds "..." --mode api --dry-run
```

Offline, from your own exports (SEO Minion SERP sheets, DataForSEO/Semrush
keyword exports):

```bash
python scripts/fishing.py \
  --seeds "forklift battery" "lithium forklift battery" \
  --mode offline \
  --offline ideas=exports/ideas.csv \
            "serp=exports/serp_*.csv" \
            domains=exports/competitor_keywords.csv \
  --out out/
```

SERP exports may be one file per keyword (the filename becomes the keyword) or
one combined file with a `keyword` column. Column names are matched
case-insensitively against common aliases — `Keyword`/`关键词`, `Volume`/`搜索量`,
`KD`/`Difficulty`/`竞争难度`, `Intent`/`搜索意图`, `URL`, `Position`/`Rank`.

## Outputs

| File | Contents |
|------|----------|
| `step1_ideas.csv` | all expanded candidates from the seeds |
| `sheet1.csv` | top-30 on-topic high-traffic keywords (4 params) |
| `step2_serp_hits.csv` | every kept organic result: keyword, position, domain, url |
| `step2_serp_dropped.csv` | every rejected result **with reason** |
| `step2_domains.csv` | competitor domains with breadth and best position |
| `sheet2.csv` | competitor-ranked keywords, on-topic only, with domain + position |
| `step3_domain_summary.csv` | per-domain pulled / dropped counts |
| `sheet3.csv` | **the deliverable** — deduped, 4 params + sources |
| `fishing_mode_sheets.xlsx` | sheet1 / sheet2 / sheet3 in one workbook |

## Cost Control

Roughly: ideas `$0.05`, each SERP `$0.005`, each domain's ranked keywords
`$0.05`. Defaults (20 SERPs + 15 domains) land near **$0.90** per run. Levers,
cheapest first:

1. `--serp-targets` — fewer keywords means fewer SERPs
2. `--max-domains` — the domain step is the expensive one
3. `--domain-kw-limit` — 100 per the SOP; 50 halves the returned rows, not the price
4. Cache: re-runs hit the API again, so keep `out/` and re-merge offline instead

## Common Pitfalls

1. **Trusting token overlap for relevance.** Any single shared word passes a naive
   filter, so `car battery` rides in on *battery*. Require the industry qualifier
   (the token in every seed) **and** the category noun.
2. **"Discard tokens common to all seeds."** Feels like generic-word removal;
   actually deletes `forklift`, the industry token, and rejects the
   highest-volume keyword on the list. Commonsense-looking fix that silently
   destroys output. Full four-attempt history in
   `references/relevance-filter-iterations.md`.
3. **Being generously inclusive.** `lithium battery` and `48v battery` read as
   related to a lithium forklift battery business and are a different product
   page. Adjacent-category keywords belong to other products — drop them.
4. **Spot-checking only the dropped rows.** Every broken filter version kept
   some junk out successfully. The damaging bug is always the flagship keyword
   silently removed, so assert must-keep and must-drop in the same test.
5. **Skipping the junk filter.** Without it, sheet2 becomes an Amazon/Walmart
   category dump and the final table is useless. Always read
   `step2_serp_dropped.csv` once to confirm the blocklist matches your market.
6. **Not filtering competitor keywords.** Step 03 returns what a competitor ranks
   for, including adjacent products. Filter, or sheet3 inherits solar panels and
   golf-cart batteries.
7. **Seeds with no shared qualifier.** If the 5 seeds share only `battery`, the
   derived industry token is empty and `--context` becomes mandatory. Pass
   `--context forklift` explicitly in that case.
8. **Seeding with brand + category terms.** `yourbrand forklift battery` returns
   navigational keywords with no commercial value for a new page.
9. **Naive `-s` plural stripping.** `batteries` → `batterie` never merges with
   `battery`, so sheet3 double-counts. Handle `-ies → -y` first, and exclude
   `-ss` / `-us` / `-is` endings before stripping.
10. **SERP depth ≠ rows.** Depth 50 returns up to 50 organic items but fewer when
    the SERP is ad/feature-heavy; don't assert an exact count.
11. **Assuming offline SERP files carry keywords.** SEO Minion sheet-per-keyword
    exports have no keyword column — the filename is the query. A parser that
    requires the column silently yields zero rows.
12. **Burning budget on a cold seed set.** Run `--dry-run`, then one seed at a time
    with `--serp-targets 2` to sanity-check relevance before the full run.
13. **Feeding a seed list into a `*args` parameter.** A `cross_dedupe(*sources)`
    signature swallowed the seed list as a third data source and crashed on
    `str.get`. Use keyword-only arguments for anything that takes both a
    variable collection and a scalar config value.
14. **`--context` short-circuiting the relevance test.** Checked first as
    "keyword contains any context term → pass", so `--context forklift` let
    `forklift certified` (a training course), `forklift rental near me`, and
    `forklift propane tank` into sheet3. A context term may only *rescue* a
    keyword that also carries the category noun — never replace that check.
15. **`keyword_ideas` as the step-01 source.** It is associative, not vertical:
    seeded with forklift terms it returned `smoke detector battery` (201k) and
    `car battery` (74k) at the top, and the 5 seeds with volume 0. Use
    `keyword_suggestions` per seed (keeps context, returns real seed volume) and
    merge ideas behind the filter.
16. **Reading `url` / `domain` from a live Google SERP.** DataForSEO emits
    `domain: "google.com"` and `url: "https://google.com/goto?url=CAES..."` for
    every organic item; the whole competitor list became google.com. The real
    host survives only in `breadcrumb` (`https://www.site.com › section`). Strip
    the scheme, split on `›`, then validate.
17. **Trusting `breadcrumb` blindly.** YouTube items carry `8.5k+ views · 6 years
    ago`, which becomes a fake target and kills step 03 with
    `Invalid Field: 'target'`. Gate every domain candidate through a hostname
    regex, and catch per-domain errors so one bad target cannot abort the batch.
18. **`max_crawl_pages: 1` on SERP requests.** Starves parsing: 8 organic rows
    returned for a depth-50 request, and it looked like the filter had eaten
    everything. Omit the parameter. `40101 Internal SE Server Error` is transient
    — retry with backoff, then skip that keyword.

## Verification Checklist

- [ ] `python scripts/selftest.py` exits 0 before any paid run
- [ ] 5 seeds, all sharing one industry token; no brand names
- [ ] `sheet1.csv` ≤ 30 rows, all on-topic for the product
- [ ] `step2_serp_dropped.csv` exists and every row has a `reason`
- [ ] `step2_domains.csv` contains real competitors, no marketplace/directory domains
- [ ] `sheet2.csv` free of adjacent-category keywords (solar, golf cart, car)
- [ ] `sheet3.csv` has exactly keyword / intent / volume / difficulty (+ sources)
- [ ] No duplicate keyword in `sheet3.csv` after singular/plural normalisation
- [ ] `fishing_mode_sheets.xlsx` opens with three sheets and auto-filters on
- [ ] Row counts reported by the script match the files

## Attribution

Method: SEO小平 / 隔壁老王, 2023-03-12 深圳独立站SEO大会 — 捕鱼模式的SEO
(01 以词找词 / 02 用词找站 / 03 筛选利基). This skill is an independent
implementation of that SOP; the automation, filtering and dedup logic are
original.
