# DataForSEO endpoints used by this skill

All calls are `POST https://api.dataforseo.com/v3/<path>` with HTTP Basic auth
(`DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`) and a **JSON array** body containing
one task object. The script reads `tasks[0].result[0]`.

Response shapes below were confirmed against live responses in 2026-09; DataForSEO
adds fields over time but is stable on the ones used here.

---

## Step 01 — keyword ideas

`dataforseo_labs/google/keyword_ideas/live`

```json
[{
  "keywords": ["forklift battery", "lithium forklift battery"],
  "location_code": 2840,
  "language_code": "en",
  "limit": 500,
  "include_serp_info": false
}]
```

Read per item:

| Field | Meaning |
|-------|---------|
| `keyword` | the candidate |
| `keyword_info.search_volume` | monthly volume |
| `keyword_properties.keyword_difficulty` | 0–100 KD |
| `search_intent_info.main_intent` | `informational` / `commercial` / `transactional` / `navigational` |

Cost ≈ **$0.05** per request regardless of seed count (billed per task).

---

## Step 02 — live SERP

`serp/google/organic/live/advanced`

```json
[{
  "keyword": "lithium forklift battery",
  "location_code": 2840,
  "language_code": "en",
  "depth": 50,
  "max_crawl_pages": 1
}]
```

`result[0].items[]` holds one object per SERP element. Filter to
`item.type == "organic"`, then:

| Field | Meaning |
|-------|---------|
| `url` | full result URL — the domain is parsed from it |
| `domain` | bare host (present, but re-derive it) |
| `rank_group` | position among organic results — **use this**, not `rank_absolute` |
| `rank_absolute` | position including ads/features — wrong for "top 50 organic" |
| `title` | page title |

Cost ≈ **$0.005** per keyword. `depth` caps how many elements are returned; a
feature-heavy SERP yields fewer than `depth` organic items.

---

## Step 03 — a domain's ranked keywords

`dataforseo_labs/google/ranked_keywords/live`

```json
[{
  "target": "bigbattery.com",
  "location_code": 2840,
  "language_code": "en",
  "limit": 100,
  "order_by": ["keyword_data.keyword_info.search_volume,desc"],
  "include_serp_info": false
}]
```

Each item nests the keyword under `keyword_data`, and the ranking under
`ranked_serp_element.serp_item`:

| Field | Meaning |
|-------|---------|
| `keyword_data.keyword` | the keyword |
| `keyword_data.keyword_info.search_volume` | volume |
| `keyword_data.keyword_properties.keyword_difficulty` | KD |
| `keyword_data.search_intent_info.main_intent` | intent |
| `ranked_serp_element.serp_item.rank_group` | the domain's organic position |
| `total_count` (result level) | how many keywords the domain ranks for in total |

Cost ≈ **$0.05** per domain. `limit: 100` follows the SOP's "top 100"; the price
per request does not change much with limit, so pulling 100 is the economical choice.

---

## Failures worth handling

| `status_code` | Meaning | Script reaction |
|---------------|---------|-----------------|
| `20000` | ok | proceed |
| `40104` | bad/missing credentials | abort with the message |
| `40200` | payment required / account issue | abort |
| `40204`, `40205` | access denied / rate limit | abort, do not retry blindly |
| `>= 40000` | task-level failure | retry twice with backoff, then log and continue |

The script never lets one failed task kill the run: the affected step logs the
error, writes whatever succeeded, and the merge step proceeds with what exists.

---

## Location and language codes

Common codes for `location_code`:

| Code | Location |
|------|----------|
| 2840 | United States |
| 2826 | United Kingdom |
| 2124 | Canada |
| 2036 | Australia |
| 2276 | Germany |
| 2250 | France |
| 2392 | Japan |
| 2152 | Singapore |

`language_code` uses ISO-639-1 plus variants (`en`, `de`, `fr`, `ja`, `zh-CN`).

Set `--location` to the market you actually sell into. Pulling a US SERP for a
German product produces a competitor list you cannot use.

---

## Alternatives to the API

The `--mode offline` path accepts exports from:

- **SEO Minion** — "SERP" tab, download results for the current Google query.
  One file per keyword, columns `position` / `title` / `url` (no keyword column;
  the filename supplies it).
- **DataForSEO dashboard** — any "Export" button on Labs / SERP pages (CSV/XLSX).
- **Semrush / Ahrefs** — "Keyword Explorer" and "Organic keywords" exports.

Required columns: `keyword` + `volume` (plus optionally `difficulty`, `intent`,
`url`, `position`). Missing `difficulty`/`intent` are written as 0/empty rather
than faked.
