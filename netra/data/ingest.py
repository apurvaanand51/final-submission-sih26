"""
Turning a file into a capture, and reporting honestly what was done to it.

READERS, THEN ONE NORMALISER
----------------------------
Four readers (delimited text, JSON, XML, SQLite) exist only to produce rows. They
all hand off to a single normaliser, so there is exactly one place where a value
becomes a timestamp, an amount or an address list. Two normalisers would drift,
and the drift would show up as two pages disagreeing about the same file.

THE GATE IS THE PRODUCT
-----------------------
Rows we cannot interpret are counted and given a reason, never dropped in silence.
In an investigation a missing row is a missing fact, and "we used 96% of your file"
is a claim that has to be checkable. The gate runs before anything expensive, and
its report is what the operator sees first.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from netra.data.capture import (
    CANONICAL_FIELDS,
    IngestReport,
    is_placeholder,
    map_columns,
    normalise_header,
)

# A delimited file may use any of these; sniffing beats demanding one.
CANDIDATE_SEPARATORS = (",", ";", "\t", "|")
# Timezones, as they appear in the wild: +02:00, +0200, Z.
_TZ_PRESENT = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)\s*$")
# Separators inside a list-valued cell. JSON arrays and pipe/comma/semicolon lists
# all occur in real exports.
_LIST_SPLIT = re.compile(r"\s*[|;,]\s*")


def _sniff_separator(sample: str) -> str:
    """Pick the separator that yields the most columns on the first line."""
    first = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {sep: first.count(sep) for sep in CANDIDATE_SEPARATORS}
    best = max(counts, key=lambda sep: counts[sep])
    return best if counts[best] else ","


def read_rows(path: Path) -> tuple[list[dict[str, Any]], list[str], str]:
    """Read any supported file into `(rows, headers, reader_name)`.

    Rows are plain dicts in the file's own vocabulary. Nothing is renamed here:
    the mapping is a separate, reported step, and mixing the two is how a reader
    ends up quietly guessing what a column means.
    """
    suffix = path.suffix.lower()

    if suffix in (".csv", ".txt", ".tsv", ".dat"):
        sample = path.read_text(encoding="utf-8", errors="replace")[:65536]
        if not sample.strip():
            return [], [], "csv"
        separator = "\t" if suffix == ".tsv" else _sniff_separator(sample)
        frame = pd.read_csv(path, sep=separator, dtype=str, keep_default_na=False,
                            engine="python", on_bad_lines="skip")
        return frame.to_dict("records"), [str(c) for c in frame.columns], "csv"

    if suffix in (".json", ".jsonl", ".ndjson"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix in (".jsonl", ".ndjson"):
            # One JSON object per line. Real capture tooling emits this because it
            # streams, and a whole-capture file that never fits in memory is exactly
            # the kind of file an investigation produces. Each line is read on its
            # own, and a line that is not an object is counted rather than silently
            # skipped -- see the `skipped` count below.
            rows: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip().rstrip(",")
                if not line or line in ("[", "]"):
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
            headers = sorted({key for row in rows for key in row})
            return rows, headers, "json"
        payload = json.loads(text)
        # `{"records": [...]}` and a bare list both occur.
        if isinstance(payload, dict):
            for key in ("records", "rows", "data", "transactions", "items"):
                if isinstance(payload.get(key), list):
                    payload = payload[key]
                    break
        if not isinstance(payload, list):
            raise ValueError("JSON must be a list of objects, or an object containing one")
        rows = [row for row in payload if isinstance(row, dict)]
        headers = sorted({key for row in rows for key in row})
        return rows, headers, "json"

    if suffix == ".xml":
        tree = ET.parse(path)
        records: list[dict[str, Any]] = []
        # Any repeated child element is a record; the schema is not assumed.
        for parent in tree.iter():
            groups: dict[str, list[ET.Element]] = {}
            for child in parent:
                groups.setdefault(child.tag, []).append(child)
            for tag, elements in groups.items():
                if len(elements) < 2:
                    continue
                for element in elements:
                    record: dict[str, Any] = {}
                    for leaf in element.iter():
                        if leaf is not element and len(leaf) == 0 and leaf.text:
                            record.setdefault(leaf.tag, leaf.text.strip())
                    if record:
                        records.append(record)
                if records:
                    headers = sorted({key for row in records for key in row})
                    return records, headers, "xml"
        return [], [], "xml"

    if suffix in (".sqlite", ".sqlite3", ".db"):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall()]
            if not tables:
                raise ValueError("the database contains no tables")
            # The largest table is almost always the transfer log; a lookup table
            # of ten rows is not what the operator is uploading.
            counts = {name: connection.execute(
                f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in tables}
            table = max(counts, key=lambda name: counts[name])
            frame = pd.read_sql_query(f'SELECT * FROM "{table}"', connection)
        finally:
            connection.close()
        return frame.to_dict("records"), [str(c) for c in frame.columns], "sqlite"

    raise ValueError(
        f"unsupported file type '{suffix or path.name}' -- expected .csv, .txt, "
        ".tsv, .json, .jsonl, .ndjson, .xml, .sqlite, .sqlite3 or .db")


def _as_list(value: Any) -> list[str]:
    """Split a cell into address parts, tolerating every separator in use.

    A JSON array, a pipe list, a comma list and a single value all appear in real
    exports of the same underlying data.
    """
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return []
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text or is_placeholder(text):
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            decoded = json.loads(text.replace("'", '"'))
            if isinstance(decoded, list):
                return [str(item).strip() for item in decoded if str(item).strip()]
        except json.JSONDecodeError:
            pass
    return [part for part in _LIST_SPLIT.split(text) if part and not is_placeholder(part)]


def _as_amounts(value: Any) -> list[float]:
    """Split a cell into amounts, dropping the parts that are not numbers.

    Returns what it could parse rather than raising, because the caller decides
    whether a row with one unreadable amount is rejectable -- and a single bad
    figure in a ten-output transaction should not discard the other nine.
    """
    parts: list[Any]
    if isinstance(value, (list, tuple, np.ndarray)):
        parts = list(value)
    else:
        text = "" if value is None else str(value).strip()
        if text.startswith("[") and text.endswith("]"):
            try:
                decoded = json.loads(text.replace("'", '"'))
                parts = list(decoded) if isinstance(decoded, list) else [text]
            except json.JSONDecodeError:
                parts = _LIST_SPLIT.split(text)
        else:
            parts = _LIST_SPLIT.split(text)
    amounts: list[float] = []
    for part in parts:
        if part is None or is_placeholder(part):
            continue
        try:
            amounts.append(float(str(part).replace(",", "").strip()))
        except (TypeError, ValueError):
            continue
    return amounts


def _looks_like_satoshis(series: pd.Series) -> bool:
    """Is this column almost certainly satoshis rather than bitcoin?

    A real risk with real files: 100,000,000 is one bitcoin. A capture in
    satoshis would be read as values a hundred million times too large, and the
    model would see an economy that does not exist. The test is deliberately
    conservative -- median above 100,000 with an integer-looking distribution --
    because a false conversion is worse than no conversion, and the report states
    the decision either way.

    The median is taken over rows that CARRY a value. A row with no amount is
    silence, not evidence of a small number: on a real export where most rows have
    one side empty, including those zeros in the median drags it under the
    threshold and the whole file is then read in the wrong unit -- the exact
    failure this function exists to prevent.
    """
    values = pd.to_numeric(series, errors="coerce").dropna()
    values = values[values > 0]
    if values.empty or len(values) < 20:
        return False
    median = float(values.median())
    if median < 100_000:
        return False
    fractional = np.mean(np.abs(values - np.round(values)) > 1e-9)
    return bool(fractional < 0.01)


def to_capture(rows: list[dict[str, Any]], report: IngestReport) -> pd.DataFrame:
    """Normalise raw rows into the canonical capture, counting what it drops."""
    if not rows:
        return pd.DataFrame(columns=list(CANONICAL_FIELDS))

    headers = list(rows[0].keys())
    resolved, mappings = map_columns(headers)
    report.mappings = mappings
    report.total = len(rows)

    if "timestamp" not in resolved:
        raise ValueError(
            "no timestamp column found -- a capture without time cannot be "
            "analysed. Recognised names include: timestamp, time, datetime, date")
    if not any(field in resolved for field in ("input_addresses", "output_addresses")):
        raise ValueError(
            "no address column found -- a capture without addresses cannot "
            "produce a graph. Recognised names include: input_addresses, inputs, "
            "from_address, output_addresses, outputs, to_address")

    frame = pd.DataFrame(rows)

    # ---- timestamps -------------------------------------------------------
    raw_time = frame[resolved["timestamp"]].astype(str)
    has_zone = raw_time.str.strip().str.contains(_TZ_PRESENT, na=False).any()
    stamps = pd.to_datetime(raw_time, utc=True, errors="coerce")
    if not has_zone:
        # Said out loud rather than silently: treating a local time as UTC shifts
        # every batch boundary, and the batches are the monitoring unit.
        report.timezone_assumed = "UTC"
    bad_time = stamps.isna()

    capture = pd.DataFrame({"timestamp": stamps})

    for canonical, source in resolved.items():
        if canonical == "timestamp":
            continue
        column = frame[source]
        if canonical in ("input_addresses", "output_addresses"):
            capture[canonical] = column.apply(_as_list)
        elif canonical in ("input_amounts", "output_amounts"):
            capture[canonical] = column.apply(_as_amounts)
        else:
            capture[canonical] = column.apply(
                lambda value: "" if is_placeholder(value) else str(value).strip())

    for field in CANONICAL_FIELDS:
        if field not in capture.columns:
            capture[field] = [] if field.endswith(("addresses", "amounts")) else ""

    # ---- the gate ---------------------------------------------------------
    reject = pd.Series(False, index=capture.index)

    if bad_time.any():
        report.reject("timestamp could not be read", int(bad_time.sum()))
        reject |= bad_time

    no_transfer = capture["input_addresses"].apply(len).eq(0) & \
        capture["output_addresses"].apply(len).eq(0)
    if no_transfer.any():
        report.reject("no addresses on either side of the transfer", int(no_transfer.sum()))
        reject |= no_transfer

    negative = capture["input_amounts"].apply(
        lambda values: any(value < 0 for value in values)) | \
        capture["output_amounts"].apply(lambda values: any(value < 0 for value in values))
    if negative.any():
        report.reject("negative amount", int(negative.sum()))
        reject |= negative

    # A single-amount file: fill whichever side is empty, so a row that carries
    # value on one side is not read as a zero-value transfer.
    if "input_amounts" in resolved and "output_amounts" in resolved and \
            resolved["input_amounts"] == resolved["output_amounts"]:
        same = capture["input_amounts"].apply(len).eq(0) & \
            capture["output_amounts"].apply(len).gt(0)
        capture.loc[same, "input_amounts"] = capture.loc[same, "output_amounts"]

    # ---- satoshis ---------------------------------------------------------
    for field in ("input_amounts", "output_amounts"):
        flat = capture[field].apply(lambda values: float(np.sum(values)) if values else 0.0)
        if _looks_like_satoshis(flat):
            divisor = 100_000_000.0
            capture[field] = capture[field].apply(
                lambda values: [value / divisor for value in values])
            report.amount_unit_note = (
                "amounts look like satoshis (median above 100,000 and integral); "
                "converted to bitcoin by dividing by 100,000,000")

    capture = capture.loc[~reject].reset_index(drop=True)
    report.accepted = int(len(capture))
    report.rejected = int(reject.sum())

    # ---- duplicates -------------------------------------------------------
    txid = capture["txid"].astype(str)
    real = txid.ne("") & txid.ne("nan")
    if real.any():
        duplicates = int(real.sum() - txid[real].nunique())
        if duplicates:
            report.note("rows sharing a transaction id (kept: a transfer with "
                        "several outputs is several rows)", duplicates)

    if not capture.empty:
        report.span_start = str(capture["timestamp"].min())
        report.span_end = str(capture["timestamp"].max())

    missing_geo = int((capture["geo_country"] == "").sum())
    if missing_geo and missing_geo == len(capture):
        report.note("no country information in the file: wallet groups will have "
                    "no geography", missing_geo)
    missing_amounts = int(capture["input_amounts"].apply(len).eq(0).sum())
    if missing_amounts == len(capture) and len(capture):
        report.note("no amounts in the file: transaction values are unavailable, "
                    "so value-based features are zero", missing_amounts)
    elif missing_amounts:
        # Kept, because a transfer with an unknown value is still a transfer -- an
        # address that received funds from another is a fact, and dropping the row
        # would delete the link. Counted, because "no amount" is not "zero".
        report.note("rows with no readable amount (kept as transfers of unknown "
                    "value)", missing_amounts)
    missing_sender = int(capture["input_addresses"].apply(len).eq(0).sum())
    if missing_sender and missing_sender < len(capture):
        report.note("rows with no sender address (kept: the receiving side still "
                    "records the transfer)", missing_sender)

    # One `value` column states the transaction's total, not what each output got.
    # The link between the addresses is still evidence; the split is not in the
    # file, so it is not invented here either -- and the operator is told which
    # rows are affected rather than finding out from a report that looks quiet.
    for side in ("input", "output"):
        addresses = capture[f"{side}_addresses"].apply(len)
        amounts = capture[f"{side}_amounts"].apply(len)
        ragged = int((amounts.gt(0) & addresses.ne(amounts)).sum())
        if ragged:
            report.note(f"rows gave one amount covering several {side} addresses "
                        "(the links are kept; the per-address split is not in the "
                        "file, so those transfers carry no value)", ragged)

    return capture


def load_capture(path: Path | str) -> tuple[pd.DataFrame, IngestReport]:
    """Read a file and report what was made of it. The public entry point."""
    path = Path(path)
    if not path.exists():
        raise ValueError(f"file not found: {path}")

    report = IngestReport(source=path.name, reader="unknown")
    rows, _headers, reader = read_rows(path)
    report.reader = reader
    capture = to_capture(rows, report)

    if capture.empty and report.total:
        # Every row was rejected: say which reason dominated, because a file with
        # one systemic problem is a one-line fix for the operator.
        worst = max(report.rejections.items(), key=lambda kv: kv[1]) if report.rejections else None
        detail = f" (most common reason: {worst[0]}, {worst[1]} rows)" if worst else ""
        raise ValueError(f"no usable rows in {path.name}{detail}")

    return capture, report


def write_capture(frame: pd.DataFrame, path: Path) -> None:
    """Write a capture in the uploadable format: list-valued cells pipe-joined,
    timestamps ISO-8601, and NO labels or entity ids.

    The last part is the discipline that keeps the evaluation honest -- if the
    answer leaked into this file, every measured metric would be meaningless.
    """
    out = frame.copy()
    for field in ("input_addresses", "output_addresses"):
        out[field] = out[field].apply(lambda values: "|".join(str(v) for v in values))
    for field in ("input_amounts", "output_amounts"):
        out[field] = out[field].apply(
            lambda values: "|".join(f"{float(v):.8f}" for v in values))
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).dt.strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    out = out[list(CANONICAL_FIELDS)]
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
