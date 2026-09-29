#!/usr/bin/env python
"""
The acceptance path: read a real capture, and report what the workstation did to it.

WHAT THIS IS FOR
----------------
A demonstration that only works on the dataset it shipped with proves nothing. The
question a buyer, an examiner or an investigating officer actually asks is: "take
my file -- the one my tool exported, with its own column names and its own units --
and show me what you make of it."

So this tool does exactly that, in the order the workstation does it, and prints
every step rather than a verdict:

    1. READ      which columns were recognised, and under which canonical field
    2. GATE      how many rows were usable, and a reason for every row set aside
    3. UNITS     whether amounts arrived in satoshis, and whether times had a zone
    4. ABSENT    which of the fields the analysis uses were not in the file, and
                 what that costs
    5. ANALYSE   optionally: run the windowed pipeline and show the leads it found

Steps 1-4 need no models and no store, so they are always safe to run and they are
where a surprising result is usually explained. Step 5 writes to the state store
and is therefore opt-in.

    python tasks.py accept uploads/my_capture.csv            # read and report only
    python tasks.py accept uploads/my_capture.csv --analyse   # then analyse it
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netra import config                                    # noqa: E402
from netra.data.ingest import load_capture                  # noqa: E402


def _rule(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _report_reading(report) -> None:
    """What the reader made of the file, field by field."""
    _rule("1. READ -- how your columns were understood")
    print(f"  reader        {report.reader}")
    print(f"  rows read     {report.total:,}")
    if report.span_start:
        print(f"  time span     {report.span_start}  to  {report.span_end}")

    mapped = [entry for entry in report.mappings if entry.action in ("mapped", "derived")]
    ignored = [entry for entry in report.mappings if entry.action == "ignored"]
    if mapped:
        width = max(len(str(entry.source or "")) for entry in mapped)
        print(f"\n  {'your column':<{width}}  ->  canonical field")
        for entry in mapped:
            suffix = f"   ({entry.note})" if entry.note else ""
            print(f"  {str(entry.source or ''):<{width}}  ->  {entry.canonical}{suffix}")
    if ignored:
        print("\n  columns present but not used by the analysis:")
        for name in sorted(str(entry.source) for entry in ignored):
            print(f"    {name}")


def _report_gate(report) -> None:
    """Every row that did not make it, with the reason it did not."""
    _rule("2. GATE -- what was usable, and what was set aside")
    print(f"  usable        {report.accepted:,} of {report.total:,}  "
          f"({report.usable_share:.1%})")
    if report.rejections:
        for reason, count in sorted(report.rejections.items(), key=lambda kv: -kv[1]):
            print(f"  set aside     {count:>7,}  {reason}")
    else:
        print("  set aside          0  every row was interpretable")
    for note, count in sorted(report.notes.items(), key=lambda kv: -kv[1]):
        print(f"  note          {count:>7,}  {note}")


def _report_units(report) -> None:
    _rule("3. UNITS -- amounts and time")
    if report.amount_unit_note:
        print(f"  amounts       {report.amount_unit_note}")
    else:
        print("  amounts       used as given; no unit conversion was needed")
    if report.timezone_assumed:
        print(f"  timestamps    {report.timezone_assumed}")
    else:
        print("  timestamps    carried a UTC offset; none was assumed")


def _report_absent(report) -> None:
    """The honest part: what the file did not contain, and what that costs."""
    _rule("4. ABSENT -- fields the analysis uses that your file did not have")
    absent = [entry for entry in report.mappings if entry.action == "absent"]
    if not absent:
        print("  nothing material was missing.")
        return
    width = max(len(entry.canonical) for entry in absent)
    for entry in absent:
        print(f"  {entry.canonical:<{width}}  {entry.note}")


def _analyse(path: Path) -> int:
    """The opt-in half: the same pipeline the workstation runs, on this file."""
    from netra.operations.pipeline import replay

    _rule("5. ANALYSE -- the windowed pipeline on this capture")
    print("  this writes to the analysis store; a capture is analysed as a whole,")
    print("  so the store is reset (see netra/operations/pipeline.py).\n")
    result = replay(store_path=config.STATE_DB, dataset=path,
                    data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR, reset=True)

    print(f"  {'batch':<30} {'tx':>7} {'groups':>8} {'new':>5} {'events':>7} {'alerts':>7}")
    for window in result["windows"]:
        print(f"  {window['label']:<30} {window['transactions']:>7} "
              f"{window['entities']:>8} {window['new_entities']:>5} "
              f"{window['events']:>7} {window['alerts']:>7}")

    from netra.operations.payload import payload_for
    from netra.state.store import MonitoringStore

    with MonitoringStore(config.STATE_DB) as store:
        payload = payload_for(store, "all", default="all", models_dir=config.MODELS_DIR)

    groups = [entity for entity in payload["entities"] if entity.get("kind") != "ip"]
    flagged = sorted(
        (entity for entity in groups if int(entity.get("risk") or 0) >= config.DEFAULT_REVIEW_FLOOR),
        key=lambda entity: (-int(entity["risk"]), -float(entity.get("confidence") or 0)))

    _rule("LEADS -- what the workstation would put in front of an analyst")
    print(f"  {len(flagged)} of {len(groups)} wallet groups are at or above the review "
          f"floor of {config.DEFAULT_REVIEW_FLOOR}.")
    if flagged:
        print(f"\n  {'id':<12} {'risk':>4} {'conf':>6}  {'band':<9} {'typology':<26} top driver")
        for entity in flagged[:25]:
            typology = ", ".join(entity.get("typology") or []) or "-"
            features = entity.get("features") or []
            driver = features[0]["name"] if features else "-"
            print(f"  {entity['id']:<12} {int(entity['risk']):>4} "
                  f"{float(entity.get('confidence') or 0):>6.3f}  "
                  f"{str(entity.get('risk_band')):<9} {typology[:26]:<26} {driver}")
        if len(flagged) > 25:
            print(f"  ... {len(flagged) - 25} more")

        # The attribution has to reconcile, because that is what the interface
        # promises: base + named + smaller factors == the score on screen.
        worst = None
        for entity in flagged:
            explanation = entity.get("explanation")
            if not explanation:
                continue
            named = sum(item["importance"] for item in (entity.get("features") or []))
            total = (explanation["base"] + named
                     + explanation.get("other_contribution", 0.0))
            gap = abs(total - explanation["prediction"])
            if worst is None or gap > worst[1]:
                worst = (entity["id"], gap)
        if worst:
            print(f"\n  attribution reconciles to within {worst[1]:.2e} of the stated "
                  f"score (worst case: {worst[0]})")
    print("\n  A lead is not a finding of guilt. It is a group of addresses the machine")
    print("  could not explain, with the reason it could not.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", help="the file to read")
    parser.add_argument("--analyse", action="store_true",
                        help="after reading, run the windowed pipeline on it")
    parser.add_argument("--json", action="store_true", help="also print the report as JSON")
    args = parser.parse_args(argv)

    path = Path(args.capture)
    if not path.exists():
        print(f"no such file: {path}")
        return 1

    print("\n  NETRA acceptance path")
    print(f"  file          {path}")
    print(f"  size          {path.stat().st_size / 1024:,.1f} KiB")
    print(f"  engine        {config.ENGINE_VERSION}   contract {config.SCHEMA_VERSION}")

    try:
        frame, report = load_capture(path)
    except ValueError as exc:
        print(f"\n  REFUSED: {exc}\n")
        print("  The file was not read. Nothing was scored, and nothing was guessed.\n")
        return 2

    _report_reading(report)
    _report_gate(report)
    _report_units(report)
    _report_absent(report)

    if args.json:
        _rule("REPORT AS JSON")
        print(json.dumps(report.as_dict(), indent=2, default=str))

    print(f"\n  capture held in memory: {len(frame):,} rows x {len(frame.columns)} columns\n")

    if args.analyse:
        return _analyse(path)

    print("  Nothing was scored. Re-run with --analyse to run the pipeline on it.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
