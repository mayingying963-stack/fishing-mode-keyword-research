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

捕鱼模式 treats every keyword as one net: cast more nets, catch more traffic.

The method is a five-step funnel that ends with a single deduplicated table of
keywords you can actually rank for. The volume chain is the point — each step
multiplies:

```
01 以词找词   5 seed keywords  → 5 x 30 = 150 candidate keywords      (sheet1)
02 用词找站   150 keywords      → 150 x 50 = 7,500 SERP results
              → junk removed → unique registrable domains
03 用站找词   every surviving domain → its top 100 ranked keywords     (sheet2)
04 交叉去重   sheet1 (150) + all domain keywords → one row per keyword (sheet3)
05 筛选利基   optional: narrow sheet3 to the niche worth attacking
```

Do not shortcut the chain. Capping step 02 at "the top 20 keywords" turns a
150-keyword sweep into a 20-keyword sample and the domain list stops being the
full competitor set. `--serp-targets 0` (default) and `--max-domains 0` (default)
mean "run them all", which is the SOP behaviour; the flags exist only for a
cheap trial run.

The script does all five steps and writes every intermediate file, so a failed
or expensive step never costs you the work before it.

**The one rule that decides output quality:** steps 02 and 03 both need to know
what "related to my product" means. Sharing the category noun is not enough —
`car battery` and `solar battery` both contain *battery*, and a forklift-battery
competitor will happily rank for both. The script derives the industry qualifier
from tokens present in **every** seed (`forklift`) and treats the trailing noun
as the category (`battery`); a keyword must carry both. Seed your 5 keywords so
that at least one token truly appears in all of them.

Two readings of that rule exist and only one is correct — do not confuse them:

- **Required** = present in every seed (`forklift`). The keyword must contain it.
  Keep this set.
- **Discard if common to all seeds** = the trap. Sounds like the same idea, but it
  deletes `forklift` and keeps only `battery`, which then rejects the highest-volume
  correct keyword. That is pitfall #2 and the subject of
  `references/relevance-filter-iterations.md`.

The distinction is *required vs. generic*: a token in every seed is a
requirement; the category noun is the requirement you must **not** treat as
disqualifying. Implemented in `relevant()` as `required <= kset` plus
`category & kset`.

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
python scripts/selftest.py            # end-to-end pipeline, temp fixtures
python scripts/verify_field_shapes.py # live response shapes, no spend
```

`verify_field_shapes.py` is the companion probe: it locks the exact wire shapes
that real DataForSEO responses have (redirect-wrapped SERP domains, YouTube
breadcrumbs, `keywords` vs `keyword` payloads, `keyword_data` nesting) because
every mismatch there produced a plausible-looking but wrong table rather than an
error. Run both after touching `relevant()`, `domain_from_serp_item()`,
`step1_ideas()`, `step3_domain_keywords()`, or the dedup path.

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

The script runs `dataforseo_labs/google/keyword_suggestions/live` **once per seed**
(`limit: max(per_seed*2, 100)`, `include_seed_keyword: true`) and writes
`step1_ideas.csv`. Per-seed calls are what preserve each seed's context — a single
combined call loses it and drifts into adjacent categories.

Default budget is **30 candidates per seed** (`--per-seed-ideas 30`), so 5 seeds
yield ~150 rows. Do not pool the seeds into one shared budget: the loudest seed
then starves the other four.

`keyword_ideas` is only a top-up for seeds that `keyword_suggestions` could not
bring up to `per_seed`. When you do call it, pass `"keywords": [seed]` (array) —
`keyword_ideas/live` does not honour a singular `"keyword"` key and silently
returns nothing, which looks like "this seed has no more keywords".

*Done when:* `step1_ideas.csv` shows every seed with roughly its own 30
candidates, and every seed keyword appears with its **real** search volume (a
seed row with volume 0 means it was injected as a placeholder, not returned).

### 02 用词找站 — candidates to SERP domains

Two sub-steps from the original SOP, in one command:

1. keep the on-topic, highest-volume candidates → `sheet1.csv`. `--sheet1-keep`
   is a **per-seed** cap (default 30), so 5 seeds × 30 = up to 150 rows. A global
   cap would let one loud seed consume the whole table.
2. pull `serp/google/organic/live/advanced` at depth 50 for **every** sheet1
   keyword and drop junk domains.

**Default is "run everything", not a sample.** `--serp-targets 0` and
`--max-domains 0` both mean *no cap*, which is the SOP behaviour and what the
user asked for ("按 30 个词跑 serp，不用节约成本"). Only set a number when the
user explicitly wants a cheap trial. Watch the `[:0]` trap: slicing a list with a
cap of 0 drops every row, so the cap must be applied as
`if cap: rows = rows[:cap]`, never `rows[:cap]` unconditionally.

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

Automated, full SOP volume (the default — no caps):

```bash
python scripts/fishing.py \
  --seeds "forklift battery" "lithium forklift battery" "forklift battery charger" \
          "48v forklift battery" "forklift battery replacement" \
  --context forklift \
  --location 2840 --language en \
  --out out/
```

Trial run on a fraction of the SERPs, to sanity-check relevance cheaply:

```bash
python scripts/fishing.py --seeds "..." --serp-targets 3 --max-domains 2 --out trial/
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
| `sheet1.csv` | on-topic high-traffic keywords (4 params), up to 30 **per seed** |
| `step2_serp_hits.csv` | every kept organic result: keyword, position, domain, url |
| `step2_serp_dropped.csv` | every rejected result **with reason** |
| `step2_domains.csv` | competitor domains with breadth and best position |
| `sheet2.csv` | competitor-ranked keywords, on-topic only, with domain + position |
| `step3_domain_summary.csv` | per-domain pulled / dropped counts |
| `sheet3.csv` | **the deliverable** — deduped, 4 params + sources |
| `关键词调研_最终表.xlsx` | the handover workbook — four real sheets, see "Deliverable Shape" |

## Cost Control

`keyword_suggestions` is ~$0.015 per seed, each SERP $0.005, each domain's
ranked keywords $0.05. The SOP chain is 5 seeds → 150 candidates → 150 SERPs →
every surviving domain, so cost scales with the domain count:

| Step | Calls | Unit | Subtotal |
|------|-------|------|----------|
| 01 candidates | 5 | ~$0.015 | $0.08 |
| 02 SERPs | 150 | $0.005 | $0.75 |
| 03 domain keywords | N domains | $0.05 | $0.05 × N |

A measured forklift-battery run (5 seeds, 150 SERPs, 15 domains) cost **$0.83**;
a full 30-domain sweep lands near **$1.60**. Both are inside a $50 balance.

Default intent is *no cap* — that is the SOP. Levers when the user wants a cap,
cheapest first:

1. `--serp-targets N` — SERPs are the bulk of the volume
2. `--max-domains N` — the domain step is the most expensive per unit
3. `--domain-kw-limit` — 100 per the SOP; lowering it halves returned rows but
   barely moves the price, because billing is per request
4. Cache: re-runs hit the API again, so keep `out/` and re-merge offline instead

A cap changes what the method produces: `--serp-targets 20` turns a 150-keyword
sweep into a 20-keyword sample and the domain list stops being the full
competitor set. Use caps for a trial run, not for the deliverable.

## Deliverable Shape — an XLSX with four real sheets

The intermediate `sheet1/sheet2/sheet3.csv` files are for auditing the pipeline.
The **deliverable is one `.xlsx` workbook with four real sheet tabs**, in this
order and with these exact Chinese names:

| Sheet | Columns |
|-------|---------|
| `种子词拓展` | 关键词, 搜索意图, 搜索量, 竞争难度 |
| `以站找词` | 关键词, 搜索意图, 搜索量, 竞争难度, 来源域名, 排名 |
| `去重后可用词` | 关键词, 搜索意图, 搜索量, 竞争难度, 来源 |
| `同行域名` | 域名, 覆盖关键词数, 命中次数, 最佳排名 |

**Do not build this as a single CSV with `=== 标题 ===` separator rows.** That
shape was delivered once and rejected outright: "文件中所有的都放在第一个sheet中，
不是这样子，是分开的表sheet放在同一个csv中". A CSV has no tabs, so writing every
block into one column-stream means any spreadsheet app shows one flat sheet. If
the user says "分开的表放在同一个文件里", they mean workbook tabs — build `.xlsx`
via `openpyxl` (`wb.create_sheet(name)` per block), and clarify only if you
genuinely cannot tell.

Rules that matter:

- **同行域名 is not `step2_domains.csv`.** That file holds every domain that
  survived the junk filter (791 in the measured run). The sheet carries only the
  domains **actually mined in step 03** — the ones judged valuable peers. Build
  it by intersecting with `competitor_domain` values present in sheet2, then
  sort by `-覆盖关键词数, 最佳排名`.
- **Translate intent codes to Chinese** (`T→交易, C→商业, I→信息, N→导航`) in the
  delivered workbook; keep the raw codes in the CSVs.
- `freeze_panes = "A2"` + `auto_filter.ref = ws.dimensions` + explicit column
  widths per header, header row filled `1F4E79` with white bold — the user reads
  these files directly, so they must look finished.
- **Also write an ASCII-named copy** (`fishing_mode_<product>_final.xlsx`) beside
  any Chinese-named file — Chinese filenames frequently fail to double-click open
  on this user's machine.
- Before writing, sweep sheet1/sheet2/sheet3 for residual adjacent-vertical
  keywords (`solar`, `golf`, `marine`, `propane`, `certified`, `rental`). The
  strict sheet1 rule only requires a seed's tokens to appear, so
  `48v forklift battery for solar` passes it — that is a solar-storage product,
  not a traction battery. Drop them before generating the workbook.

Keep a CSV of each block in the run directory as the audit trail; the workbook is
what gets handed over.

`scripts/export_workbook.py` builds this workbook from a finished run directory:

```bash
python scripts/export_workbook.py out/                          # -> out/关键词调研_最终表.xlsx
python scripts/export_workbook.py out/ -o report.xlsx
python scripts/export_workbook.py out/ --min-breadth 3          # peers covering >=3 keywords
```

It takes the **run directory as a positional argument** (not `--dir`), reads
`sheet1/sheet2/sheet3/step2_domains.csv` from it, derives 同行域名 by
intersecting with the domains actually mined, and localises intent automatically
— there is no `--intent-cn` flag. It exits 2 if the directory is missing or has
no `sheet3.csv`, so a typo fails loudly instead of writing an empty workbook.

## Cost Decisions Belong to the User

Step 02 routinely surfaces far more domains than expected — the measured
forklift run produced **790 unique registrable domains** from 150 SERPs, of which
400 appeared for only one keyword. Mining all of them at $0.05 each is **$39.5**,
which is 20x a capped run and shocks the balance.

So: **do not silently truncate and do not silently run it all.** Print the real
count, the real price, and offer graded choices in one message. On the measured
run the options were 790 domains ($39.5) / breadth≥2 390 ($19.5) / breadth≥3
247 ($12.4) / top-30 trial; the user picked ≥3 at $12.4 and the actual charge
came in at $5.51 because many domains returned few on-topic rows.

`--min-breadth N` exists for exactly this: domains appearing for fewer than N of
your keywords are mostly unrelated sites that ranked once. Default 2; 3 is a
good "real topical competitors" line. Report the cost **after** the run from
`dfs.spend`, not the estimate — the estimate has been wrong in both directions
(it once priced a fixed 30 domains while the chain mined 790).

## Practice Run — Reconciling Numbers With the Stated SOP

This user asks for a self-check and challenges any gap between the counts and
the SOP as written ("真实数据跑通的在数据数量上和我发给你逻辑验证的存在误差").
Answer by **tabulating spec vs actual per stage**, not with prose assurance:

| Stage | Spec | Actual |
|-------|------|--------|
| seeds | 5 | 5 |
| per-seed candidates | 30 each → ~150 | 112–144 (some seeds genuinely lack 30 on-topic terms) |
| SERP depth | 50 per keyword | request depth 50, organic median ~39 (Google returns fewer) |
| domains mined | every surviving site | set by the user's cost decision — state it explicitly |
| sheet3 | 4 params, deduped | state row count and total volume |

Then name which gaps are **your bug** vs **the data source's limit**. Being
vague here reads as covering up; the user will push again.

Two more things that made this exchange work on the second attempt:

- **Re-report from the files on disk, not from memory.** The first answer quoted
  "51 keywords" from a run that had since been overwritten by a rerun; the real
  files said 113/1837/216. Read row counts out of the CSVs in the same turn you
  answer, and say which directory you read.
- **Own the mistakes explicitly.** When the gap is a cap you passed
  (`--max-domains 15` against a 790-domain set) or a rule you chose, say so in
  one line and fix it, instead of explaining it away as a data limit. The user's
  trust recovered on the answer that led with "两个是我的错".

## Common Pitfalls

1. **Trusting token overlap for relevance.** Any single shared word passes a naive
   filter, so `car battery` rides in on *battery*. Require the industry qualifier
   (the token in every seed) **and** the category noun.
2. **"Discard tokens common to all seeds."** Feels like generic-word removal;
   actually deletes `forklift`, the industry token, and rejects the
   highest-volume keyword on the list. Commonsense-looking fix that silently
   destroys output. Full four-attempt history in
   `references/relevance-filter-iterations.md`.
3. **Applying the strict rule to competitor keywords.** This one cost an entire
   run before it was caught. Step 02 needs the strict rule (require the industry
   qualifier) or `car battery` floods sheet1. Step 03 must **not** use it: fed a
   competitor's own keyword list, the strict rule kept **0 of 100** for
   `leochlithium.us`, `redwaypower.com`, `malinusa.com` and `innovdepot.com` —
   every lithium rival surfaced by our own SERPs — because their lists read
   `lithium battery`, `lifepo4 battery`, `48v lithium battery` with no "forklift".
   `relevant_competitor()` is the inverted rule: keep category tokens, drop only
   named adjacent verticals (solar, golf, car, marine, RV, phone, laptop, UPS…).
   A domain only reaches step 03 by ranking for our keywords, which is already
   proof it is in the business.
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
    `keyword_suggestions` per seed (keeps context, returns real seed volume);
    call `keyword_ideas` only as a per-seed top-up, and pass **`"keywords": [seed]`**
    — the singular `"keyword"` key is ignored and returns an empty item list,
    which reads as "this seed is exhausted" rather than as a bug.
    A seed row with **volume 0** is the tell that it was injected as a
    placeholder instead of returned by the API: check for it before trusting
    sheet1, because sorting by volume silently parks it at the bottom.
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
19. **Docs drifting behind the code (the expensive one).** After the step-01
    source changed from `keyword_ideas` to per-seed `keyword_suggestions`, and
    the caps changed from "20/15" to "no cap", this SKILL.md still described the
    old behaviour. A fresh session reading it would have "fixed" the code back
    to the version that produced drifted, off-topic keywords. When you change a
    default, a source endpoint, or a cap's scope, **patch SKILL.md in the same
    turn** — the pitfalls list, the step description, the command recipe, the
    output table, and the cost section all restate those values.
20. **Verifying a file that changed underneath you.** `fishing.py` was edited by
    another writer after being committed (100 insertions / 38 deletions). A test
    run reported 83/83 green while the log line it printed did not exist in the
    version that had been read earlier — that mismatch was the only signal.
    Always `git status --short` before claiming verification, and treat a log
    message you do not recognise as evidence you are testing different code.
21. **A function returning a different tuple than its caller unpacks.**
    `serp_top_domains()` was changed to `return hits, collect_domains(hits)` while
    the caller still did `hits, dropped = serp_top_domains(...)`. The junk filter
    itself worked (zero amazon/youtube rows in `hits`), but `dropped` became the
    *domain table*, so `step2_serp_dropped.csv` was 791 domains with empty reason
    columns. Nothing raised, every row count looked plausible, and the audit trail
    silently vanished. When a helper's return type changes, grep for **every**
    call site in the same edit — and assert in the self-test that the dropped file
    contains a `reason` on each row.
22. **`step2_sheet1` collapsing rows that carry no seed prefix.** Grouping was
    `src.split(":", 1)[1] if ":" in src else src`, so rows whose `source` was
    `sheet1` (offline exports, a re-read of a written CSV) all landed in one
    bucket and the per-seed cap became a whole-table cap of 30. Fixed by
    attributing un-prefixed rows to the seed with the largest token overlap.
    Corollary: keep `source` in `step1_ideas.csv` (write it with the `trace`
    header, not the 4-column `sheet1` header) so a rerun can regroup accurately
    instead of guessing from keyword text.
23. **`relevant_competitor()` only excluding *verticals*, not *services*.** The
    inverted step-03 rule kept category tokens and dropped adjacent verticals, so
    `forklift certified` (a training course), `forklift rental near me` (a service)
    and `forklift operator job` sailed through into sheet3 — they contain no
    adjacent vertical, only the industry token. Add a separate
    `NON_PRODUCT_INTENT` set (`certified, certification, training, course, rental,
    rent, hire, lease, operator, job, salary, career, school, exam`) and reject on
    it **before** the vocab check, in both rules. The self-test caught this only
    because the step-03 fixture included `forklift certified`; when you widen a
    filter's vocabulary, widen its rejection fixtures at the same time.
24. **Sourcing seed tokens from the expanded sheet instead of the seeds.**
    `export_workbook.py` derived the adjacent-vertical exemption list from
    `sheet1.csv`, so a keyword that had already drifted —
    `48v forklift battery for solar` — contributed `solar` to the exempt list and
    then exempted *itself*. Read seed tokens from the real seeds only
    (`seeds.txt`, else the `source` column of `step1_ideas.csv`), and when no
    source is available fall back to **strict** (exempt nothing): a missed
    exemption drops one keyword, a spurious one admits a whole wrong vertical.
    Symptom to watch for: the workbook's row count is one or two higher than the
    CSV it was built from.

## Verification Checklist

- [ ] `python scripts/selftest.py` exits 0 before any paid run
- [ ] `python scripts/verify_field_shapes.py` exits 0 after touching the filter,
      SERP-domain, step-01, step-03, or dedup code
- [ ] `git status --short` clean before claiming verification (the file may have
      been edited by another writer — see pitfall #20)
- [ ] 5 seeds, all sharing one industry token; no brand names
- [ ] `sheet1.csv` all on-topic, up to 30 rows **per seed** (5 seeds → ≤150)
- [ ] `step2_serp_dropped.csv` exists and every row has a `reason`
- [ ] `step2_domains.csv` contains real competitors, no marketplace/directory domains
- [ ] `sheet2.csv` free of adjacent-category keywords (solar, golf cart, car)
- [ ] `sheet3.csv` has exactly keyword / intent / volume / difficulty (+ sources)
- [ ] No duplicate keyword in `sheet3.csv` after singular/plural normalisation
- [ ] Deliverable built as **one `.xlsx` with four real sheet tabs** (种子词拓展 /
      以站找词 / 去重后可用词 / 同行域名), each with frozen header row + autofilter,
      plus an ASCII-named copy
- [ ] 同行域名 contains only domains actually mined in step 03, not all of
      `step2_domains.csv`
- [ ] Workbook opened once and inspected: `wb.sheetnames` has exactly those four
      names, and each sheet's `max_row > 1` (a "delivered" workbook with empty
      tabs is the failure mode this checklist exists to catch)
- [ ] `step2_serp_dropped.csv` rows each carry a non-empty `reason` (see pitfall 21)
- [ ] Candidate total reported as "≤150", never asserted as exactly 150: per-seed
      requests for narrow seeds (`48v …`, `… replacement`) genuinely return fewer
      than 30 terms. 112–144 out of a nominal 150 is correct output, not a bug —
      do not "fix" it by loosening the relevance rule.
- [ ] Row counts reported by the script match the files

## Attribution

Method: SEO小平 / 隔壁老王, 2023-03-12 深圳独立站SEO大会 — 捕鱼模式的SEO
(01 以词找词 / 02 用词找站 / 03 筛选利基). This skill is an independent
implementation of that SOP; the automation, filtering and dedup logic are
original.
