#!/usr/bin/env python3
"""
Field Experiment Paper Collection Pipeline

Usage examples:
  python collect.py --source all --year-from 2000
  python collect.py --source journals --journal AER
  python collect.py --source journals --year-from 2010
  python collect.py --source aea
  python collect.py --source jpal
  python collect.py --source bit

Run from the Field-Experiment-AI-Agent directory:
  python -m data_collection.collect --source all
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_collection import storage
from data_collection.config import JOURNAL_ISSNS
from data_collection.collectors import aea_rct, bit, jpal, openalex


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Collect field experiment papers from journals and databases"
    )
    p.add_argument(
        "--source",
        choices=["all", "journals", "aea", "jpal", "bit"],
        default="all",
        help="Which data source(s) to collect from",
    )
    p.add_argument(
        "--journal",
        choices=list(JOURNAL_ISSNS.keys()),
        default=None,
        help="Restrict to one journal (only with --source journals)",
    )
    p.add_argument(
        "--year-from",
        type=int,
        default=2000,
        dest="year_from",
        help="Earliest publication year to include (default: 2000)",
    )
    return p


def main() -> None:
    args = build_parser().parse_args()
    storage.ensure_dirs()
    manifest = storage.load_manifest()
    all_records = []
    counts: dict[str, int] = {}

    if args.source in ("all", "journals"):
        journals_to_run = (
            {args.journal: JOURNAL_ISSNS[args.journal]}
            if args.journal
            else JOURNAL_ISSNS
        )
        records = openalex.fetch_all_journals(args.year_from, manifest, journals_to_run)
        all_records.extend(records)
        counts["openalex"] = len(records)

    if args.source in ("all", "aea"):
        records = aea_rct.collect_aea(args.year_from, manifest)
        all_records.extend(records)
        counts["aea_registry"] = len(records)

    if args.source in ("all", "jpal"):
        records = jpal.collect_jpal(args.year_from, manifest)
        all_records.extend(records)
        counts["jpal"] = len(records)

    if args.source in ("all", "bit"):
        records = bit.collect_bit(args.year_from, manifest)
        all_records.extend(records)
        counts["bit"] = len(records)

    if all_records:
        n = storage.save_csv(all_records)
        storage.update_log(counts)
        print(f"\nDone. {len(all_records)} new records merged into:")
        print(f"  {storage.CSV_PATH}")
        for src, cnt in counts.items():
            print(f"  {src}: {cnt}")
    else:
        print("\nNo new records collected.")


if __name__ == "__main__":
    main()
