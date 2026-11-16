# 捕鱼法 · Fishing-Mode Keyword Research

A one-command implementation of **SEO小平's 捕鱼模式 (Fishing Mode)** keyword
research SOP — turn 5 seed keywords into one clean, de-duplicated keyword table.

Ship it as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) skill, or
run `scripts/fishing.py` standalone. No framework required for the script.

```
01 以词找词  5 seeds → keyword ideas
02 用词找站  top-30 on-topic keywords → Google top-50 SERP → drop junk domains
03 用站找词  each competitor domain → its top-100 ranked keywords → sheet2
04 交叉去重  sheet1 + sheet2 → one row per keyword (4 params) → sheet3
05 筛选利基  optional: narrow sheet3 to the niche you can rank in
```

## Why the filtering matters more than the crawling

Pulling SERPs and competitor keywords is easy. The hard part — and the reason
most "keyword universe" exports are unusable — is deciding what is *on topic*:

- `car battery` and `solar battery` share the token *battery* with
  `forklift battery`. Naive overlap lets them through.
- A forklift-battery competitor also ranks for solar and golf-cart batteries.
  Export its keywords unfiltered and your final table inherits them.
- Marketplace and video results (`amazon`, `walmart`, `youtube`) dominate
  commercial SERPs and would otherwise become your "competitor list".

This tool derives the **industry qualifier** from tokens present in *every* seed
(`forklift`) and the **category noun** from the trailing word (`battery`), then
requires both. Every dropped SERP result is logged with a reason so you can
audit the judgement.

## Install

```bash
git clone https://github.com/<you>/fishing-mode-keyword-research.git
pip install requests openpyxl
```

As a Hermes skill, drop the folder into `~/.hermes/skills/keyword-research/`.
Hermes picks it up on the next session.

## Run

```bash
export DATAFORSEO_LOGIN=you@example.com
export DATAFORSEO_PASSWORD='your-dataforseo-api-password'

# estimate first — prints cost and exits
python scripts/fishing.py --seeds "forklift battery" "lithium forklift battery" \
    "forklift battery charger" "48v forklift battery" "forklift battery replacement" \
    --mode api --dry-run

# full run
python scripts/fishing.py --seeds "forklift battery" "lithium forklift battery" \
    "forklift battery charger" "48v forklift battery" "forklift battery replacement" \
    --location 2840 --serp-targets 20 --max-domains 15 --out out/
```

### No DataForSEO account?

Use exports from SEO Minion, Semrush, Ahrefs or the DataForSEO dashboard:

```bash
python scripts/fishing.py \
  --seeds "forklift battery" "lithium forklift battery" \
  --mode offline \
  --offline ideas=exports/ideas.csv \
            "serp=exports/serp_*.csv" \
            domains=exports/competitor_keywords.csv \
  --out out/
```

Column names are matched case-insensitively with English and Chinese aliases
(`Keyword`/`关键词`, `Volume`/`搜索量`, `KD`/`竞争难度`, `Intent`/`搜索意图`).
SERP files may be one-per-keyword (filename becomes the query) or combined with a
`keyword` column.

## Output

| File | What |
|------|------|
| `sheet1.csv` | top-30 on-topic keywords from seed expansion |
| `sheet2.csv` | competitor-ranked keywords, on-topic only |
| `sheet3.csv` | **final table**: keyword, intent, volume, difficulty, sources |
| `step2_serp_dropped.csv` | every excluded SERP result + reason |
| `fishing_mode_sheets.xlsx` | sheet1/2/3 in one workbook, filtered and formatted |

## Cost

About **$0.90** per default run (ideas $0.05 + 20 SERPs × $0.005 + 15 domains ×
$0.05). `--dry-run` prints the estimate without calling the API.

## Options

| Flag | Default | Meaning |
|------|---------|---------|
| `--seeds` | — | 5 seed keywords |
| `--seeds-file` | — | one seed per line |
| `--context` | none | extra on-topic terms, e.g. `forklift` |
| `--mode` | `api` | `api` or `offline` |
| `--location` | `2840` | DataForSEO location code (2840 = US) |
| `--language` | `en` | language code |
| `--sheet1-keep` | `30` | rows kept in sheet1 |
| `--serp-targets` | `20` | how many sheet1 keywords get a SERP pull |
| `--serp-depth` | `50` | organic depth per SERP |
| `--domain-kw-limit` | `100` | keywords pulled per domain |
| `--max-domains` | `15` | domains sent to step 03 |
| `--extra-exclude` | none | additional junk domains |
| `--niche-filter` | off | also filter sheet3 |
| `--out` | `out` | output directory |
| `--dry-run` | off | estimate cost, exit |

## Before you trust the output

- [ ] 5 seeds share one industry token; no brand names in seeds
- [ ] `sheet1.csv` ≤ 30 rows and every row is on-topic
- [ ] `step2_serp_dropped.csv` has a reason for every dropped row
- [ ] `sheet2.csv` contains no adjacent-category words (solar, golf cart, car)
- [ ] `sheet3.csv` has no duplicate after singular/plural normalisation

## Field notes from real runs

These are actual failures hit while running the tool against live DataForSEO
data on a US forklift-battery project. They are the reason the code looks the
way it does — each one silently produced a plausible but wrong table.

| Symptom | Real cause | Fix in this repo |
|---------|-----------|------------------|
| sheet3 full of `forklift certified`, `forklift rental`, `forklift propane tank` | `--context forklift` short-circuited the relevance test, so any word containing it passed | context terms can only rescue a keyword that **also** carries the category noun |
| sheet1 only had the 5 seeds, volume 0 | `keyword_ideas` is associative and drifted to smoke-detector / car batteries at 200k volume | `keyword_suggestions` per seed is primary; ideas are merged behind the filter |
| every competitor domain was `google.com` | Google's current layout makes DataForSEO emit a redirect wrapper in `url` and `domain` | real host parsed from `breadcrumb`, validated with a hostname regex |
| competitor list full of `8.5k+ views · 6 years ago` | YouTube breadcrumbs are view counts, not domains | `looks_like_domain()` gate before any domain is used |
| step 03 aborted with `Invalid Field: 'target'` | a non-domain leaked into the target list | targets validated up front; per-domain failures logged, never fatal |
| `batteries` and `battery` never merged | naive `-s` stripping turned `batteries` into `batterie` | proper `-ies → -y` plural handling |
| SERP returned 0 rows | `max_crawl_pages: 1` truncated item parsing | parameter removed; `40101` retried with backoff |

Run `python scripts/selftest.py` (67 assertions, no API key needed) before
trusting a change.

## Attribution

Method: **SEO小平 / 隔壁老王**, 2023-03-12 深圳独立站SEO大会 — 捕鱼模式的SEO,
steps 01 以词找词 / 02 用词找站 / 03 筛选利基.
This repository is an independent implementation: the automation, relevance
filtering, dedup and output pipeline are original work.

## License

MIT — see [LICENSE](LICENSE).
