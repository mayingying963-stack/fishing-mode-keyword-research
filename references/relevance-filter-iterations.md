# Relevance filtering: why it takes four attempts

The single highest-risk piece of this pipeline. Recorded because the naive
implementations all *look* correct and all silently pollute the output table.

## The failure class

"Related to my product" sounds obvious and is not. Four successive
implementations were written and each passed a casual eyeball check while being
wrong. The bug is only visible when you assert on both directions at once:
must-keep AND must-drop, on the same seed set.

Use a fixed test matrix, never ad-hoc spot checks. The matrix that finally
worked: 14 must-keep keywords, 12 must-drop, checking both with and without
`--context`, plus a single-seed case.

## Attempt 1 — naive token overlap

```python
seed_tokens = set of all words in all seeds
return bool(seed_tokens & keyword_tokens)
```

Every seed contains `battery`, so `battery` sits in `seed_tokens` and
`car battery`, `solar battery`, `golf cart battery` all pass.

**Result: 9 of 30 cases wrong.**

## Attempt 2 — exclude tokens shared by *all* seeds

Reasoning: `battery` is in every seed, so it is generic, so drop it from the
discriminating set.

```python
generic = set.intersection(*seed_token_sets)
discriminating = union(*seed_token_sets) - generic
```

**Fails the other way.** In `["forklift battery", "lithium forklift battery",
"forklift battery charger", "48v forklift battery", "forklift battery
replacement"]`, BOTH `forklift` and `battery` appear in all five. So `forklift`
— the single most important product token — gets discarded as "generic", and
the core keyword `forklift battery` (8100 volume) is **rejected**. sheet1
collapsed from 20 rows to 5, dropping the head term.

**Result: 5 of 30 wrong, including the flagship keyword.**

The lesson: "appears in all seeds" does not mean "uninformative". Here it meant
"this is the industry" — the opposite conclusion.

## Attempt 3 — require category noun AND a modifier

```python
category = {tail word of each seed}      # battery
modifiers = {non-tail seed words}        # forklift, lithium, charger, 48v
return bool((category & kw) and (modifiers & kw))
```

`forklift battery` now correctly passes (category battery + modifier forklift).
But `lithium battery`, `48v battery`, `battery charger`, `lithium ion battery`
also pass — they have the noun and one modifier, just not the *industry* one.

**Result: 5 of 30 wrong.** `lithium battery` is genuinely adjacent to a
lithium-forklift-battery business, which is what makes this attempt seductive —
it feels like it is being appropriately generous. It is not: those keywords
belong to other products.

## Attempt 4 — the version that works

Separate the two roles explicitly:

```python
required  = tokens present in EVERY seed       # forklift, battery
modifiers = tokens present in only SOME seeds  # lithium, 48v, charger
category  = the required token that is the tail word in most seeds  # battery

# pass if: a whole seed phrase is contained in the keyword (token-set ⊆)
# otherwise: ALL required tokens present, AND category present
```

- `forklift battery` → phrase match → pass
- `lithium forklift battery` → phrase match → pass
- `electric forklift battery` → has forklift + battery → pass
- `lithium battery` → missing `forklift` → **drop** (correct)
- `lithium mining` → missing `battery` → **drop** (correct)
- `car battery` → missing `forklift` → **drop** (correct)

**Result: 30/30, and 0/16 failures with `--context` removed entirely**, proving
the rule stands on the seed set alone rather than leaning on the manual context
hint. Single-seed input also verified.

## Transferable rules

1. **Assert both directions in a fixed matrix.** "It filters out the junk" and
   "it keeps the good stuff" are separate claims; every broken attempt above
   passed one while failing the other.
2. **Never discard a token just because it is common.** Frequency identifies
   the category noun *and* the industry qualifier, and those play opposite
   roles. Rank by role, not by rarity.
3. **Test the flagship keyword explicitly.** Attempt 2 silently killed the
   highest-volume term on the seed list — the most damaging possible bug, and
   a spot check of the *dropped* junk would never reveal it.
4. **Re-run with `--context` empty.** If the rule only works with the manual
   hint supplied, the seed logic is not doing its job.
5. **A generous filter is a bug.** Adjacent-category keywords feel related
   (`lithium battery` for a lithium forklift battery seller) but belong to a
   different product page. Require the industry token.

## Dedup counterpart bug

`norm_kw` was stripping a bare `-s`, turning `batteries` into `batterie` while
`battery` stayed intact — so the plural and singular never merged and sheet3
double-counted every such keyword. Plural handling needs the `-ies → -y` rule
before the plain `-s` rule, and `-ss`/`-us`/`-is` endings excluded from the
strip. The selftest asserts `norm_kw("48v-forklift-battery") == norm_kw("48v
forklift batteries")` specifically.

## Status at hand-off

The Attempt-4 relevance logic is verified 30/30. The `norm_kw` rewrite was
applied but **the selftest was not re-run after it** — three dedup assertions
were still red at the end of that session. Run
`python scripts/selftest.py` as the first action before trusting or shipping
this skill; it needs no API key and costs nothing.
