# Making the relevance rules product-agnostic

The filter was built and tuned against one seed set (forklift battery). It looked
general — no `forklift` literal anywhere in the logic — but "no hardcoded word"
is not the same as "works for any product". Auditing it against four unrelated
verticals surfaced three real defects that the forklift case could never show.

Run this audit before using the skill on a new product category. It needs no API
key and costs nothing.

## The four audit verticals

| Vertical | Seeds | What it stresses |
|----------|-------|------------------|
| solar inverter | `solar inverter`, `hybrid solar inverter`, `3 phase solar inverter`, `off grid solar inverter`, `solar inverter charger` | category word is *not* the last word in one seed |
| dc contactor | `dc contactor`, `high voltage dc contactor`, `dc contactor 500a`, `ev dc contactor`, `dc power contactor` | **every** seed shares one industry token → the collapse bug |
| semi module | `semi module`, `igbt semi module`, `sic semi module`, `semi power module`, `semi module supplier` | industry token is also a common English prefix (`semi truck`) |
| golf cart battery | `golf cart battery`, `lithium golf cart battery`, `48v golf cart battery` | a vertical that is *in* the seed, so exemption must apply |

For each: assert must-keep AND must-drop. The must-drop probes that matter are
the bare category word (`inverter`, `contactor`, `module`, `battery`), the
adjacent vertical (`car/solar/marine/rv/phone/laptop …`), the competing product
line (`ac` vs `dc`, `lead` vs `lithium`), and service words (`certified`,
`training`, `rental`, `jobs`).

## Defect 1 — required and category collapse into one token

When **every** seed shares the same industry word, that word ends up in both
`required` and `category`:

```
seeds: dc contactor / high voltage dc contactor / dc contactor 500a / …
freq:  contactor=5, dc=4, high=2, voltage=2, 500a=2
category  = {contactor}      (tail word in all of them)
required  = {contactor}      (in every seed)   ← same token
```

The rule degenerates to "contains the category ⇒ pass", so `contactor` and — worse
— `ac contactor` (the AC product line) passed a **DC** contactor seed set. On the
forklift seeds this never fires, because `forklift` and `battery` are two
different tokens.

Fix: compute `category` **first**, then exclude it from `required`.

```python
tail = {}                       # last token of each seed
category = {w for w, c in tail.items() if c >= ceil(n/2)}
required = {w for w, c in freq.items() if c == n and len(w) > 2 and w not in category}
```

Plus a bare-category guard: a keyword made only of category words passes only if
it **is** one of the seeds.

```python
content = kset - STOP
if category and content <= category:
    if not any(seed_set == content for seed_set in seed_sets):
        return False
```

## Defect 2 — the strict rule lacked the guards the relaxed rule had

`relevant()` (step 02) was written before `NON_PRODUCT_INTENT` and
`ADJACENT_VERTICALS` existed, and those guards were only wired into
`relevant_competitor()` (step 03). So for a non-battery product,
`inverter certified` and `car battery` passed the strict rule.

Both rules must check, in this order:

1. `NON_PRODUCT_INTENT` (service words) → reject
2. seed family tokens → must hit at least one
3. `ADJACENT_VERTICALS` minus seed tokens → reject
4. `opposites_of(seeds)` (competing product line) → reject

## Defect 3 — a vertical that is genuinely part of the product

`ADJACENT_VERTICALS` contained `truck`. For a forklift product that is **wrong**:
a forklift *is* a "forklift truck", so `battery for forklift truck` and
`forklift truck battery chargers` were rejected. Caught only by replaying the
delivered spreadsheet through the new rules.

The exemption rule is what makes this class survivable: a vertical token is
allowed when a **seed** contains it.

```python
blocked = (ADJACENT_VERTICALS & kset) - seed_tokens   # seed_tokens from the seeds
```

Words deliberately kept out of `ADJACENT_VERTICALS`, with reasons:

| Word | Why it must stay out |
|------|----------------------|
| `truck` | forklift *is* a forklift truck |
| `home`, `residential` | ordinary modifiers for solar/storage (`solar inverter for home`) |
| `solar` | only adjacent when the product is not itself solar |

## Competing product lines — `ANTONYM_GROUPS`

Hardcoding each vertical's vocabulary does not scale. What generalises is the
idea of a *conventional opposite*: if the seeds say `dc`, then `ac` is the rival
product; `lead` rivals `lithium`; `on`-grid rivals `off`-grid.

```python
ANTONYM_GROUPS = [
    {"dc", "ac"}, {"on", "off"}, {"indoor", "outdoor"}, {"wired", "wireless"},
    {"manual", "automatic", "auto"}, {"single", "three", "3"}, {"lithium", "lead"},
]

def opposites_of(seeds):
    seed_tokens = tokens of all seeds
    return union(group - seed_tokens for group in ANTONYM_GROUPS if group & seed_tokens)
```

Then `if opposites_of(seeds) & kset: return False`. This rejected `ac contactor`
without naming contactors anywhere.

## Calibrating the three vocabularies for a new category

The only manual step. When starting a new product:

1. **`ADJACENT_VERTICALS`** — list only *cross-industry* nouns that could appear
   with your category word but mean a different product (car, marine, RV, phone,
   laptop, UPS, drone, hearing aid, watch…). Do **not** list words that describe
   your own product (`truck` for forklifts, `home` for solar).
2. **`NON_PRODUCT_INTENT`** — service/intent words that are never the product:
   `certified, training, course, rental, lease, operator, job, salary, exam,
   wiring diagram`. Deliberately **not** `for sale` or `repair near me` — those
   are real commercial keywords for a dealer.
3. **`ANTONYM_GROUPS`** — add a pair only if your category has a genuine rival
   line (ac/dc, lead/lithium, single/three-phase). Never add "nice to exclude"
   words here; this set is a hard reject.

Then run the four-vertical audit plus a replay of a previous run's delivered
workbook. Target: zero must-keep losses, and any must-drop loss must be a
defensible edge case (e.g. a plural form).

## Transferred lessons

- **"No hardcoded product word" ≠ "product-agnostic".** The defects were all
  structural (token roles, missing guards), invisible in the original vertical.
- **Replay the last real deliverable** after any rule change. The `truck` bug was
  invisible in synthetic fixtures and immediately obvious on the real spreadsheet.
- **Test the shape where one industry word is shared by all seeds** — that is the
  case that breaks role assignment, and it is common in real use (`dc contactor`,
  `semi module`, `solar inverter`).
