#!/usr/bin/env python
"""
Build a foreign-shaped capture from a canonical one, to test the reader honestly.

WHY A TRANSFORM RATHER THAN A SECOND DATASET
--------------------------------------------
The claim under test is "give NETRA your file and it will tell you what it made of
it". Testing that against a file this project also generated proves nothing, and
testing it against a file whose contents are unknown makes the result uncheckable.

So this takes a capture whose contents ARE known and rewrites it the way other
tools write them:

    * a block explorer's headers      `Block Time`, `Transaction Hash`, `Sender`
    * amounts in satoshis             the wrong unit, and integer, so the reader
                                      has to notice and convert (+100,000,000)
    * timestamps with no timezone     so the gate has to say which zone it assumed
    * several addresses in one cell   separated by `;`, as multi-input exports do
    * rows that cannot be read        `N/A`, blank, `unknown` -- counted, not dropped
    * no network columns at all       IPs and countries absent, with the cost stated

Every address, amount and timestamp still comes from the source capture, so the
analysis of the transformed file can be compared with the analysis of the original
and the two must agree on what the data says.

    python tools/foreign_shape.py data/transactions.csv out/acceptance/foreign.csv
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SATOSHIS_PER_BTC = 100_000_000


def foreign_row(row: dict[str, str]) -> dict[str, str]:
    """One canonical row, respelled the way another tool would emit it."""
    inputs = [part for part in _split(row.get("input_addresses"))]
    outputs = [part for part in _split(row.get("output_addresses"))]

    def sats(value: str) -> str:
        """A whole cell, in satoshis.

        A cell can hold several values (`0.03|0.02`), and a real export states the
        transaction's value as their sum. Summing here rather than taking the first
        is what makes this fixture a test of the reader instead of a test of itself.
        """
        parts = [part for part in _split(value)]
        if not parts:
            return "unknown"
        try:
            return str(int(round(sum(float(part) for part in parts) * SATOSHIS_PER_BTC)))
        except (TypeError, ValueError):
            return "unknown"

    return {
        "Block Time": row.get("timestamp", "").replace("+00:00", "").replace("Z", ""),
        "Transaction Hash": row.get("txid", ""),
        "Sender": "; ".join(inputs),
        "Recipient": "; ".join(outputs),
        "Value": sats(row.get("output_amounts") or row.get("input_amounts") or "0"),
        "Note": "exported by chain-tools 4.2",
    }


def _split(value: str | None) -> list[str]:
    """The separators an export uses inside a cell: comma, pipe or semicolon."""
    if not value:
        return []
    text = str(value).replace("[", "").replace("]", "")
    for separator in ("|", ";", ","):
        text = text.replace(separator, " ")
    return [part for part in text.split() if part]


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) < 2:
        print(__doc__)
        return 2
    source, target = Path(argv[0]), Path(argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)

    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    written = 0
    with target.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "Block Time", "Transaction Hash", "Sender", "Recipient", "Value", "Note"])
        writer.writeheader()
        for index, row in enumerate(rows):
            out = foreign_row(row)
            # Rows a real export contains and no reader can use. Placed deterministically
            # so the expected rejection counts are known: every 500th row is broken.
            if index and index % 500 == 0:
                damage = (index // 500) % 3
                if damage == 0:
                    out["Sender"] = "N/A"
                elif damage == 1:
                    out["Block Time"] = ""
                else:
                    out["Value"] = "-"
            writer.writerow(out)
            written += 1

    print(f"\n  wrote {written:,} rows to {target}")
    print("  headers   Block Time / Transaction Hash / Sender / Recipient / Value / Note")
    print(f"  amounts   satoshis (x{SATOSHIS_PER_BTC:,}), so a correct reader must divide")
    print("  times     no timezone, so a correct reader must say which one it assumed")
    print(f"  broken    {written // 500} rows deliberately unreadable, to be counted not dropped")
    print("  absent    every network column: no IP, port, country or operator\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
