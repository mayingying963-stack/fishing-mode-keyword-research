#!/usr/bin/env python3
"""Offline self-test — no API key, no network, no cost.

Verifies the two things that actually break this pipeline:
  * the relevance rule (industry qualifier + category noun, generic nouns rejected)
  * the junk-domain filter and the end-to-end offline flow

Run:  python scripts/selftest.py
"""
from __future__ import annotations

import csv
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fishing  # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, got, want) -> None:
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}: got {got!r}, want {want!r}")


SEEDS = ["forklift battery", "lithium forklift battery", "forklift battery charger",
         "48v forklift battery", "forklift battery replacement"]


def test_relevance() -> None:
    print("\n[relevance] on-topic keywords survive")
    keep = [
        "forklift battery", "lithium forklift battery", "forklift battery charger",
        "48v forklift battery", "electric forklift battery", "24v forklift battery",
        "forklift battery price", "forklift battery suppliers", "best forklift battery",
        "battery forklift", "lithium battery for forklift", "lead acid forklift battery",
        "forklift battery watering system", "forklift battery maintenance",
    ]
    for kw in keep:
        check(kw, fishing.relevant(kw, SEEDS, []), True)

    print("\n[relevance] off-topic keywords are rejected")
    drop = [
        "car battery", "solar battery", "golf cart battery", "lithium battery",
        "battery charger", "lithium ion battery", "48v battery", "battery replacement",
        "solar panel", "electric car", "lithium mining", "aaa batteries",
    ]
    for kw in drop:
        check(kw, fishing.relevant(kw, SEEDS, []), False)

    print("\n[relevance] --context widens acceptance")
    check("scissor lift battery", fishing.relevant("scissor lift battery", SEEDS, ["scissor lift"]), True)
    check("scissor lift battery (no context)", fishing.relevant("scissor lift battery", SEEDS, []), False)

    print("\n[relevance] --context must NOT bypass the category noun")
    # Regression: passing --context forklift once let every forklift word through,
    # filling sheet3 with "forklift certified" (a training course) and
    # "forklift propane tank" (a gas cylinder).
    for kw in ["forklift", "forklift certified", "forklift rental near me",
               "forklift propane tank", "used forklifts for sale", "electric forklift",
               "toyota forklift", "forklift training"]:
        check(f"context-rejected {kw}", fishing.relevant(kw, SEEDS, ["forklift"]), False)

    print("\n[relevance] single seed still works")
    check("lithium forklift battery", fishing.relevant("lithium forklift battery", ["forklift battery"], []), True)
    check("car battery", fishing.relevant("car battery", ["forklift battery"], []), False)


def test_domains() -> None:
    print("\n[domains] junk filtering")
    junk = ["amazon.com", "www.walmart.com", "youtube.com", "ebay.com",
            "www.alibaba.com", "reddit.com", "temu.com", "wikipedia.org"]
    for d in junk:
        check(f"drop {d}", fishing.is_junk(d, set()) is not None, True)
    good = ["bigbattery.com", "foxtronbattery.com", "floridaforklifts.com",
            "www.forklift-battery.com"]
    for d in good:
        check(f"keep {d}", fishing.is_junk(d, set()), None)

    print("\n[domains] ccTLD reduction")
    check("shop.example.co.uk", fishing.base_domain("shop.example.co.uk"), "example.co.uk")
    check("www.example.com", fishing.base_domain("www.example.com"), "example.com")


def test_dedupe() -> None:
    print("\n[dedupe] plural/separator normalisation")
    check("48v-forklift-battery", fishing.norm_kw("48v-forklift-battery"), fishing.norm_kw("48v forklift batteries"))
    check("forklift battery", fishing.norm_kw("forklift battery"), fishing.norm_kw("battery forklift"))
    s1 = [{"keyword": "forklift battery", "intent": "C", "volume": 8100, "difficulty": 45, "source": "sheet1"}]
    s2 = [{"keyword": "forklift batteries", "intent": "", "volume": 8100, "difficulty": 45, "source": "bigbattery.com"},
          {"keyword": "48v forklift battery", "intent": "C", "volume": 1300, "difficulty": 31, "source": "bigbattery.com"}]
    merged = fishing.cross_dedupe(sheet1=s1, sheet2=s2, seeds=SEEDS)
    check("row count after dedupe", len(merged), 2)
    check("merged sources unioned", sorted(merged[0]["sources"].split("|")), ["bigbattery.com", "sheet1"])


def test_offline_flow() -> None:
    print("\n[offline] end-to-end run produces sheet1/sheet2/sheet3")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        ideas = tmp / "ideas.csv"
        ideas.write_text("keyword,volume,difficulty,intent\n"
                         "forklift battery,8100,45,commercial\n"
                         "lithium forklift battery,3600,38,commercial\n"
                         "solar battery,33100,58,commercial\n", encoding="utf-8")
        serp = tmp / "serp_lithium forklift battery.csv"
        serp.write_text("position,url,title\n"
                        "1,https://bigbattery.com/x,Big Battery\n"
                        "2,https://www.amazon.com/dp/1,Amazon\n"
                        "3,https://www.youtube.com/watch?v=1,Video\n", encoding="utf-8")
        doms = tmp / "domains.csv"
        doms.write_text("keyword,volume,difficulty,intent,competitor_domain,position\n"
                        "lithium forklift battery,3600,38,commercial,bigbattery.com,2\n"
                        "golf cart battery,22200,51,commercial,bigbattery.com,14\n", encoding="utf-8")
        out = tmp / "out"
        rc = fishing.main([
            "--mode", "offline", "--seeds", *SEEDS,
            "--offline", f"ideas={ideas}", f"serp={serp}", f"domains={doms}",
            "--out", str(out),
        ])
        check("exit code", rc, 0)
        for name in ("sheet1.csv", "sheet2.csv", "sheet3.csv", "step2_serp_dropped.csv"):
            check(f"{name} written", (out / name).exists(), True)

        s1 = (out / "sheet1.csv").read_text(encoding="utf-8-sig")
        check("sheet1 excludes solar", "solar" in s1, False)
        s2 = (out / "sheet2.csv").read_text(encoding="utf-8-sig")
        check("sheet2 excludes golf cart", "golf" in s2, False)
        dropped = (out / "step2_serp_dropped.csv").read_text(encoding="utf-8-sig")
        check("amazon dropped", "amazon" in dropped, True)
        check("youtube dropped", "youtube" in dropped, True)

        rows = list(csv.DictReader(io.StringIO((out / "sheet3.csv").read_text(encoding="utf-8-sig"))))
        check("sheet3 columns", list(rows[0].keys()), ["keyword", "intent", "volume", "difficulty", "sources"])
        kws = [r["keyword"] for r in rows]
        check("no duplicates in sheet3", len(kws), len(set(fishing.norm_kw(k) for k in kws)))


if __name__ == "__main__":
    test_relevance()
    test_domains()
    test_dedupe()
    test_offline_flow()
    print(f"\n{'=' * 46}\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
