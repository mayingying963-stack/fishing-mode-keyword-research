#!/usr/bin/env python3
"""Field-shape regression probe — no API key, no network, no spend.

`selftest.py` validates the pipeline end to end. This probe is narrower: it
locks the *wire shapes* that real DataForSEO responses actually have, because
every one of these mismatches produced a plausible-looking but wrong table
rather than an error.

Run after touching `domain_from_serp_item`, `relevant`, `step1_ideas`,
`step3_domain_keywords`, `cross_dedupe`, `collect_domains`, or `is_junk`:

    python scripts/verify_field_shapes.py     # expect "N passed, 0 failed"

Each case below is copied from a response observed live, not invented. If a
DataForSEO change makes one fail, the fix belongs in `fishing.py`, and the
symptom belongs in SKILL.md's pitfalls list.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fishing  # noqa: E402

PASS = FAIL = 0
FAILURES = []


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(f"{label}: got {got!r} want {want!r}")


SEEDS = ["forklift battery", "lithium forklift battery", "forklift battery charger",
         "48v forklift battery", "forklift battery replacement"]


# ---------------------------------------------------------------- SERP domains
def test_domains():
    print("[1] domain_from_serp_item / looks_like_domain")
    # Google's current layout: url and domain are a redirect wrapper; the real
    # host survives only in breadcrumb, in two different forms.
    cases = [
        ({"breadcrumb": "https://www.copowbattery.com \u203a News",
          "domain": "google.com", "url": "https://google.com/goto?url=CAESabc"},
         "copowbattery.com"),
        ({"breadcrumb": "wilsonbarrett.com \u203a product-category \u203a forklift-...",
          "domain": "google.com"}, "wilsonbarrett.com"),
        ({"breadcrumb": "https://www.polinovelpowbat.com \u203a info",
          "domain": "google.com"}, "polinovelpowbat.com"),
        # YouTube breadcrumb is a view count. Falling through to domain is
        # correct: youtube.com then gets dropped by the junk filter.
        ({"breadcrumb": "8.5k+ views \u00b7 6 years ago", "domain": "youtube.com"},
         "youtube.com"),
        ({"breadcrumb": "8.5k+ views \u00b7 6 years ago",
          "website_name": "batteryboss.com"}, "batteryboss.com"),
        ({"breadcrumb": "shop.example.co.uk \u203a category"}, "example.co.uk"),
    ]
    for item, want in cases:
        check(f"domain {(item.get('breadcrumb') or '(empty)')[:40]}",
              fishing.domain_from_serp_item(item), want)

    for host, want in [("copowbattery.com", True), ("8.5k+ views", False),
                       ("https://x.com", False), ("ebay", False),
                       ("amazoncom", False), ("a.b", False)]:
        check(f"looks_like_domain({host})", fishing.looks_like_domain(host), want)


# ------------------------------------------------------------------ relevance
def test_relevance():
    print("[2] relevant — context must not bypass the category noun")
    # The regression that filled sheet3 with training courses and gas cylinders.
    for kw in ["forklift", "forklift certified", "forklift rental near me",
               "forklift propane tank", "used forklifts for sale",
               "electric forklift", "toyota forklift", "forklift training"]:
        check(f"ctx-reject {kw}", fishing.relevant(kw, SEEDS, ["forklift"]), False)

    for kw in ["forklift battery", "lithium forklift battery",
               "forklift battery charger", "48v forklift battery",
               "electric forklift battery", "forklift battery price",
               "battery forklift", "lead acid forklift battery"]:
        check(f"ctx-keep {kw}", fishing.relevant(kw, SEEDS, ["forklift"]), True)

    for kw in ["car battery", "solar battery", "golf cart battery",
               "lithium battery", "battery charger", "lithium mining"]:
        check(f"reject {kw}", fishing.relevant(kw, SEEDS, []), False)

    # A context term should still rescue a differently-spelled on-topic product.
    check("rescue scissor lift battery",
          fishing.relevant("scissor lift battery", SEEDS, ["scissor lift"]), True)


# --------------------------------------------------------------- step1 shapes
class SuggestStub:
    """Suggestions keep context; ideas drift. Mirrors observed responses."""
    available = True

    def __init__(self):
        self.calls = []

    def post(self, path, task, retries=2):
        self.calls.append((path, dict(task)))
        if path.endswith("keyword_suggestions/live"):
            seed = task["keyword"]
            return {"items": [
                {"keyword": seed, "keyword_info": {"search_volume": 2900},
                 "keyword_properties": {"keyword_difficulty": 6},
                 "search_intent_info": {"main_intent": "transactional"}},
            ]}
        if path.endswith("keyword_ideas/live"):
            # Only honours "keywords" (array). A singular "keyword" key returns
            # an empty list — which reads as "seed exhausted", not as a bug.
            if "keywords" in task:
                return {"items": [{"keyword": "forklift battery 48v",
                                   "keyword_info": {"search_volume": 140}}]}
            return {"items": []}
        return {}


def test_step1():
    print("[3] step1_ideas — per-seed suggestions, real seed volume")
    stub = SuggestStub()
    ideas = fishing.step1_ideas(stub, SEEDS, 2840, "en")
    n_suggest = sum(1 for p, _ in stub.calls if p.endswith("keyword_suggestions/live"))
    check("one suggestions call per seed", n_suggest, len(SEEDS))
    by_kw = {r["keyword"]: r for r in ideas}
    # A seed row with volume 0 is the tell that it was injected as a placeholder.
    check("seed carries real volume", by_kw["forklift battery"]["volume"], 2900)
    check("seed carries intent", by_kw["forklift battery"]["intent"], "T")
    check("all seeds present", all(s in by_kw for s in SEEDS), True)

    sheet1 = fishing.step2_sheet1(ideas, SEEDS, ["forklift"], keep=30)
    check("sheet1 keeps forklift battery",
          "forklift battery" in [r["keyword"] for r in sheet1], True)


# --------------------------------------------------------------- step3 shapes
class DomainStub:
    available = True

    def post(self, path, task, retries=2):
        if task["target"] == "boombad.com":
            raise RuntimeError("40501 Invalid Field: 'target'.")
        return {"total_count": 1594, "items": [
            {"keyword_data": {"keyword": "forklift battery",
                              "keyword_info": {"search_volume": 2900},
                              "keyword_properties": {"keyword_difficulty": 6},
                              "search_intent_info": {"main_intent": "transactional"}},
             "ranked_serp_element": {"serp_item": {"rank_group": 4}}},
            {"keyword_data": {"keyword": "solar battery",
                              "keyword_info": {"search_volume": 33100},
                              "keyword_properties": {"keyword_difficulty": 58}},
             "ranked_serp_element": {"serp_item": {"rank_group": 14}}},
            {"keyword_data": {"keyword": "forklift certified",
                              "keyword_info": {"search_volume": 49500},
                              "keyword_properties": {"keyword_difficulty": 39}},
             "ranked_serp_element": {"serp_item": {"rank_group": 51}}},
        ]}


def test_step3():
    print("[4] step3_domain_keywords — competitor_domain + off-topic filter")
    rows, per_dom = fishing.step3_domain_keywords(
        DomainStub(), ["valueforklifts.com", "boombad.com"], 2840, "en",
        limit=100, seeds=SEEDS, extra_terms=["forklift"])
    kws = [r["keyword"] for r in rows]
    check("keeps on-topic row", "forklift battery" in kws, True)
    check("drops solar battery", "solar battery" in kws, False)
    check("drops forklift certified", "forklift certified" in kws, False)
    # Without this mapping the column writes empty and sheet2 loses provenance.
    check("competitor_domain populated",
          all(r.get("competitor_domain") == "valueforklifts.com" for r in rows), True)
    good = [d for d in per_dom if d["domain"] == "valueforklifts.com"][0]
    check("pulled counts all", good["pulled"], 3)
    check("offtopic counted", good["offtopic_dropped"], 2)
    bad = [d for d in per_dom if d["domain"] == "boombad.com"][0]
    check("bad target not fatal", bad["keywords"], 0)


# ------------------------------------------------------------------- dedupe
def test_dedupe():
    print("[5] cross_dedupe — plural merge, source union")
    s1 = [{"keyword": "forklift battery", "intent": "T", "volume": 2900,
           "difficulty": 6, "source": "sheet1"}]
    s2 = [{"keyword": "forklift batteries", "intent": "", "volume": 2900,
           "difficulty": 6, "source": "roypow.com"}]
    merged = fishing.cross_dedupe(sheet1=s1, sheet2=s2, seeds=SEEDS,
                                  extra_terms=["forklift"])
    check("plural merged", len(merged), 1)
    check("sources unioned",
          sorted(merged[0]["sources"].split("|")), ["roypow.com", "sheet1"])
    check("norm batteries == battery",
          fishing.norm_kw("forklift batteries"), fishing.norm_kw("forklift battery"))


# ------------------------------------------------------------- collect_domains
def test_collect():
    print("[6] collect_domains — keywords list, breadth ordering")
    hits = [
        {"keyword": "a battery", "position": 3, "domain": "wide.com", "url": "", "title": ""},
        {"keyword": "b battery", "position": 7, "domain": "wide.com", "url": "", "title": ""},
        {"keyword": "c battery", "position": 1, "domain": "narrow.com", "url": "", "title": ""},
    ]
    doms = fishing.collect_domains(hits)
    check("breadth-first", doms[0]["domain"], "wide.com")
    check("keyword list present",
          sorted(doms[0]["keywords"]), ["a battery", "b battery"])


if __name__ == "__main__":
    test_domains()
    test_relevance()
    test_step1()
    test_step3()
    test_dedupe()
    test_collect()
    print(f"\n{'=' * 52}\n{PASS} passed, {FAIL} failed")
    for f in FAILURES:
        print("  FAIL", f)
    sys.exit(1 if FAIL else 0)
