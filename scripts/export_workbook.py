#!/usr/bin/env python3
"""Package a finished fishing-mode run into one workbook with four sheets.

The CSV files the pipeline writes are machine-facing. This turns them into the
deliverable a human actually reviews:

    种子词拓展    seed-expansion keywords           (sheet1.csv)
    以站找词      competitor keywords               (sheet2.csv)
    去重后可用词  deduped, usable keywords          (sheet3.csv)
    同行域名      the competitor domains worth keeping (step2_domains.csv)

Usage:
    python scripts/export_workbook.py out/                    # -> out/关键词调研_最终表.xlsx
    python scripts/export_workbook.py out/ -o report.xlsx
    python scripts/export_workbook.py out/ --min-breadth 3

`--min-breadth` mirrors the CLI flag: only domains that appeared for at least N
of your keywords are listed as 同行域名, so the sheet shows real topical rivals
rather than one-off sites that ranked once.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    print("ERROR: openpyxl required ->  pip install openpyxl", file=sys.stderr)
    sys.exit(2)

# Words that name a different vertical; a battery keyword containing one of these
# belongs to another product line, even if it also contains a seed token.
ADJACENT = {"solar", "golf", "cart", "car", "auto", "marine", "boat", "rv",
            "camper", "motorcycle", "phone", "laptop", "ups", "generator",
            "hearing", "watch", "tractor", "drone", "powerwall", "residential"}
# ...unless the seed itself uses the word (a golf-cart battery business keeps it).
HARD_EXCLUDE = {"certified", "certification", "training", "course", "rental",
                "rent", "hire", "lease", "operator", "job", "jobs", "salary",
                "career", "school", "exam"}

INTENT_CN = {"T": "交易", "C": "商业", "I": "信息", "N": "导航",
             "transactional": "交易", "commercial": "商业",
             "informational": "信息", "navigational": "导航"}

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(bold=True, color="FFFFFF", size=11)
WIDTHS = {"关键词": 46, "来源域名": 28, "来源": 60, "域名": 34, "搜索意图": 10,
          "搜索量": 11, "竞争难度": 11, "排名": 9, "覆盖关键词数": 14,
          "命中次数": 11, "最佳排名": 11}
NUMERIC = {"搜索量", "竞争难度", "排名", "覆盖关键词数", "命中次数", "最佳排名"}


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def off_topic(kw: str, seed_tokens: set[str]) -> bool:
    toks = {w for w in (kw or "").lower().replace("-", " ").split() if w.isalnum()}
    if (ADJACENT & toks) - seed_tokens:
        return True
    return bool(HARD_EXCLUDE & toks)


def read_seed_tokens(src: Path) -> set[str]:
    """Tokens of the real seed keywords, in preference order.

    Sources, best first:
      * seeds.txt written next to the run
      * a `source` column in step1_ideas.csv ("suggest:<seed>")
    Falls back to nothing, which makes the adjacent-vertical filter strict — the
    safe direction: a missed exemption drops one keyword instead of admitting a
    whole wrong vertical.
    """
    seeds_txt = src / "seeds.txt"
    if seeds_txt.exists():
        return {w for line in seeds_txt.read_text(encoding="utf-8").splitlines()
                for w in line.lower().replace("-", " ").split() if w.strip()}

    tokens: set[str] = set()
    trace = load(src / "step1_ideas.csv")
    for r in trace:
        src_col = str(r.get("source") or "")
        if ":" in src_col:
            seed = src_col.split(":", 1)[1]
            tokens |= {w for w in seed.lower().replace("-", " ").split() if w.strip()}
    if tokens:
        return tokens

    # Last resort: the highest-volume row is almost always a seed itself.
    ideas = sorted(load(src / "sheet1.csv"),
                   key=lambda r: as_int(r.get("volume")) or 0, reverse=True)
    if ideas:
        return {w for w in (ideas[0].get("keyword") or "").lower().split()}
    return set()


def as_int(v) -> int | None:
    s = str(v or "").strip()
    return int(float(s)) if s.replace(".", "", 1).isdigit() else None


def build(src: Path, dest: Path, min_breadth: int) -> None:
    sheet1 = load(src / "sheet1.csv")
    sheet2 = load(src / "sheet2.csv")
    sheet3 = load(src / "sheet3.csv")
    doms = [d for d in load(src / "step2_domains.csv") if d.get("domain")]

    # Seed tokens must come from the *seeds*, not from the expanded sheet1. Taking
    # them from sheet1 lets an already-drifted keyword whitelist its own vertical:
    # "48v forklift battery for solar" contributed "solar", which then exempted
    # itself from the adjacent-vertical rule.
    seed_tokens = read_seed_tokens(src)
    f1 = [r for r in sheet1 if not off_topic(r.get("keyword"), seed_tokens)]
    f2 = [r for r in sheet2 if not off_topic(r.get("keyword"), seed_tokens)]
    f3 = [r for r in sheet3 if not off_topic(r.get("keyword"), seed_tokens)]

    mined = {r["competitor_domain"] for r in f2 if r.get("competitor_domain")}
    peer = [d for d in doms
            if d["domain"] in mined and (as_int(d.get("n_keywords")) or 0) >= min_breadth]
    peer.sort(key=lambda d: (-(as_int(d.get("n_keywords")) or 0),
                             as_int(d.get("best")) or 999))

    sheets = [
        ("种子词拓展", ["关键词", "搜索意图", "搜索量", "竞争难度"],
         [[r.get("keyword"), r.get("intent"), as_int(r.get("volume")),
           as_int(r.get("difficulty"))] for r in f1]),
        ("以站找词", ["关键词", "搜索意图", "搜索量", "竞争难度", "来源域名", "排名"],
         [[r.get("keyword"), r.get("intent"), as_int(r.get("volume")),
           as_int(r.get("difficulty")), r.get("competitor_domain"),
           as_int(r.get("position"))] for r in f2]),
        ("去重后可用词", ["关键词", "搜索意图", "搜索量", "竞争难度", "来源"],
         [[r.get("keyword"), r.get("intent"), as_int(r.get("volume")),
           as_int(r.get("difficulty")), (r.get("sources") or "")[:250]] for r in f3]),
        ("同行域名", ["域名", "覆盖关键词数", "命中次数", "最佳排名"],
         [[d["domain"], as_int(d.get("n_keywords")), as_int(d.get("hits")),
           as_int(d.get("best"))] for d in peer]),
    ]

    wb = Workbook()
    wb.remove(wb.active)
    for name, headers, rows in sheets:
        ws = wb.create_sheet(name)
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font = HEAD_FILL, HEAD_FONT
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 24
        for r in rows:
            if "搜索意图" in headers:
                i = headers.index("搜索意图")
                r[i] = INTENT_CN.get(r[i], r[i])
            ws.append(r)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = WIDTHS.get(h, 12)
            if h in NUMERIC:
                for cell in ws[get_column_letter(i)]:
                    if cell.row > 1:
                        cell.alignment = Alignment(horizontal="right")

    dest.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dest)
    print(f"-> {dest}")
    for name, headers, rows in sheets:
        print(f"  {name:10} {len(rows):>5} 行 x {len(headers)} 列")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Package a run into a 4-sheet workbook.")
    ap.add_argument("run_dir", help="directory containing sheet1/2/3 + step2_domains.csv")
    ap.add_argument("-o", "--out", default=None, help="output .xlsx path")
    ap.add_argument("--min-breadth", type=int, default=1,
                    help="min keywords a domain must cover to appear in 同行域名 (default 1)")
    args = ap.parse_args(argv)

    src = Path(args.run_dir)
    if not src.is_dir():
        print(f"ERROR: not a directory: {src}", file=sys.stderr)
        return 2
    if not (src / "sheet3.csv").exists():
        print(f"ERROR: no sheet3.csv in {src} — run the pipeline first", file=sys.stderr)
        return 2

    dest = Path(args.out) if args.out else src / "关键词调研_最终表.xlsx"
    build(src, dest, args.min_breadth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
