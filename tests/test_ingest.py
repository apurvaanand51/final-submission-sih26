"""
Ingest: reading a real file, and saying honestly what was read.

Every test here is a file shape that actually occurs in an export -- different
headers, the wrong unit, a missing timezone, several addresses in one cell, rows
that cannot be read. The acceptance proof at the demonstration runs the same code
on a real capture, so these tests are the fast version of that proof.
"""

from __future__ import annotations

import csv
import json
import sqlite3

import pandas as pd
import pytest

from netra.data.ingest import load_capture

pytestmark = pytest.mark.usefixtures("sandbox")

CANONICAL_HEADER = ["timestamp", "txid", "input_addresses", "output_addresses",
                    "input_amounts", "output_amounts", "src_ip", "dst_ip",
                    "geo_country", "asn"]


def write_csv(path, rows, header=CANONICAL_HEADER):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return path


def a_row(index=0, **overrides):
    row = {
        "timestamp": f"2026-08-11T0{index % 10}:00:00Z",
        "txid": f"tx{index:04d}",
        "input_addresses": f"bc1qin{index}",
        "output_addresses": f"bc1qout{index}",
        "input_amounts": "1.5",
        "output_amounts": "1.5",
        "src_ip": "28.24.56.211",
        "dst_ip": "76.79.110.62",
        "geo_country": "GB",
        "asn": "AS2856",
    }
    row.update(overrides)
    return [row[column] for column in CANONICAL_HEADER]


# --------------------------------------------------------------------------
# Column names from the wild
# --------------------------------------------------------------------------
def test_an_explorer_export_is_understood(sandbox):
    """`Block Time`, `Transaction Hash`, `Sender`, `Recipient`, `Value` -- the
    headers a block explorer actually writes."""
    path = write_csv(
        sandbox / "explorer.csv",
        [[f"2026-08-11T{i % 24:02d}:00:00", f"h{i}", f"bc1qfrom{i}", f"bc1qto{i}", "150000000"]
         for i in range(25)],
        header=["Block Time", "Transaction Hash", "Sender", "Recipient", "Value"])
    frame, report = load_capture(path)

    mapping = {entry.canonical: entry.source for entry in report.mappings}
    assert mapping["timestamp"] == "Block Time"
    assert mapping["txid"] == "Transaction Hash"
    assert mapping["input_addresses"] == "Sender"
    assert mapping["output_addresses"] == "Recipient"
    assert len(frame) == 25


def test_a_column_nobody_can_use_is_reported_not_dropped(sandbox):
    path = write_csv(sandbox / "extra.csv", [a_row(i) for i in range(5)],
                     header=CANONICAL_HEADER + ["Note"])
    rows = [[*a_row(i), "exported by tooling"] for i in range(5)]
    write_csv(path, rows, header=CANONICAL_HEADER + ["Note"])
    _frame, report = load_capture(path)
    ignored = {entry.source for entry in report.mappings if entry.action == "ignored"}
    assert "Note" in ignored, "a column the analysis ignores must still be named"


def test_a_single_value_column_feeds_both_directions(sandbox):
    """One `Value` column is the transaction's total. Recording it on one side
    only would halve every flow in the capture."""
    path = write_csv(sandbox / "onevalue.csv",
                     [[f"2026-08-11T0{i}:00:00", f"h{i}", f"a{i}", f"b{i}", "0.5"]
                      for i in range(25)],
                     header=["time", "hash", "from_address", "to_address", "value"])
    frame, report = load_capture(path)
    assert frame["input_amounts"].apply(len).gt(0).all()
    assert frame["output_amounts"].apply(len).gt(0).all()


# --------------------------------------------------------------------------
# Units and time
# --------------------------------------------------------------------------
def test_satoshis_are_detected_and_converted(sandbox):
    """100,000,000 is one bitcoin. A capture read in the wrong unit describes an
    economy a hundred million times larger than the real one."""
    path = write_csv(
        sandbox / "sats.csv",
        [[f"2026-08-11T0{i % 10}:00:00Z", f"t{i}", f"a{i}", f"b{i}",
          str(150_000_000 + i * 1_000_000), str(150_000_000 + i * 1_000_000)]
         for i in range(25)])
    frame, report = load_capture(path)
    assert "satoshis" in report.amount_unit_note
    assert frame["output_amounts"].iloc[0][0] == pytest.approx(1.5)


def test_bitcoin_amounts_are_left_alone(sandbox):
    """The conversion must not fire on a file already in bitcoin: a false
    conversion is worse than none."""
    path = write_csv(sandbox / "btc.csv",
                     [a_row(i, input_amounts="1.90484808",
                            output_amounts="1.90484808") for i in range(25)])
    frame, report = load_capture(path)
    assert report.amount_unit_note == ""
    assert frame["output_amounts"].iloc[0][0] == pytest.approx(1.90484808)


def test_satoshis_are_still_detected_when_most_rows_have_no_amount(sandbox):
    """A row with no amount is silence, not evidence of a small number. Taking the
    median over rows that carry a value is what keeps the wrong unit from being
    read as right."""
    rows = []
    for index in range(60):
        amount = str(200_000_000 + index * 10_000) if index % 3 == 0 else ""
        rows.append([f"2026-08-11T{index % 24:02d}:00:00Z", f"t{index}", f"a{index}",
                     f"b{index}", amount, amount])
    path = write_csv(sandbox / "sparse_sats.csv", rows)
    _frame, report = load_capture(path)
    assert "satoshis" in report.amount_unit_note, (
        "amounts in satoshis were left unconverted because most rows were blank")


def test_a_missing_timezone_is_stated_not_assumed_silently(sandbox):
    path = write_csv(sandbox / "naive.csv",
                     [[f"2026-08-11T0{i % 10}:00:00", f"t{i}", f"a{i}", f"b{i}", "1", "1"]
                      for i in range(25)])
    _frame, report = load_capture(path)
    assert report.timezone_assumed, "no assumption was reported for naive timestamps"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
def test_unreadable_rows_are_counted_with_a_reason(sandbox):
    rows = [a_row(i) for i in range(10)]
    rows.append(a_row(99, timestamp="not a date"))
    rows.append(a_row(98, input_addresses="", output_addresses=""))
    rows.append(a_row(97, input_amounts="-3", output_amounts="-3"))
    path = write_csv(sandbox / "dirty.csv", rows)
    frame, report = load_capture(path)

    assert report.total == 13
    assert report.accepted == 10
    assert report.rejected == 3
    assert len(frame) == 10
    # Every rejected row has a reason, and the reasons add up to the rejections.
    assert sum(report.rejections.values()) == 3
    assert "timestamp could not be read" in report.rejections


def test_placeholders_are_treated_as_blank_not_as_data(sandbox):
    """`N/A` parsed as a country becomes a country."""
    rows = [a_row(i, geo_country="N/A") for i in range(25)]
    path = write_csv(sandbox / "placeholders.csv", rows)
    frame, report = load_capture(path)
    assert (frame["geo_country"] == "").all()
    assert any("no country information" in note for note in report.notes)


def test_a_file_with_no_usable_rows_is_refused_with_the_reason(sandbox):
    path = write_csv(sandbox / "hopeless.csv",
                     [a_row(i, timestamp="whenever") for i in range(25)])
    with pytest.raises(ValueError) as failure:
        load_capture(path)
    assert "timestamp" in str(failure.value)


def test_a_file_without_addresses_is_refused(sandbox):
    path = write_csv(sandbox / "no_addresses.csv",
                     [[f"2026-08-11T0{i}:00:00Z", f"t{i}", "1.0"] for i in range(10)],
                     header=["timestamp", "txid", "amount"])
    with pytest.raises(ValueError) as failure:
        load_capture(path)
    assert "address" in str(failure.value)


def test_a_missing_file_is_refused(sandbox):
    with pytest.raises(ValueError):
        load_capture(sandbox / "not-here.csv")


# --------------------------------------------------------------------------
# Several addresses in one cell
# --------------------------------------------------------------------------
def test_many_addresses_in_one_cell_are_split(sandbox):
    """Real exports put several inputs in one cell, separated by commas, pipes or
    semicolons."""
    for index, separator in enumerate(("; ", "|", ", ")):
        path = write_csv(
            sandbox / f"multi_{index}.csv",
            [[f"2026-08-11T0{i % 10}:00:00Z", f"t{i}",
              separator.join([f"a{i}_1", f"a{i}_2", f"a{i}_3"]),
              f"b{i}", "1|1|1", "1"] for i in range(25)])
        frame, _report = load_capture(path)
        assert frame["input_addresses"].iloc[0] == [f"a0_1", f"a0_2", f"a0_3"], separator


def test_one_amount_covering_several_addresses_is_kept_as_unknown(sandbox):
    """The total is in the file; the per-output split is not. Dividing it by a
    rule of our own would put a number in the evidence that the file does not
    contain, so the links are kept and the amounts are not invented."""
    path = write_csv(
        sandbox / "ragged.csv",
        [[f"2026-08-11T{i % 24:02d}:00:00Z", f"t{i}", f"a{i}", f"b{i}_1|b{i}_2", "1.0", "2.0"]
         for i in range(25)])
    frame, report = load_capture(path)
    assert frame["output_addresses"].iloc[0] == ["b0_1", "b0_2"]
    assert any("one amount covering several output addresses" in note
               for note in report.notes)
    # And the same capture must survive the whole correlation step, which is where
    # a ragged pair used to raise "columns must have matching element counts".
    from netra.correlate.fuse import correlate

    correlation = correlate(frame)
    assert not correlation.entities.empty


# --------------------------------------------------------------------------
# Other formats
# --------------------------------------------------------------------------
def test_json_lines_and_json_arrays(sandbox):
    records = [dict(zip(CANONICAL_HEADER, a_row(i))) for i in range(25)]
    array_path = sandbox / "capture.json"
    array_path.write_text(json.dumps(records), encoding="utf-8")
    frame, report = load_capture(array_path)
    assert len(frame) == 25
    assert report.reader == "json"

    lines_path = sandbox / "capture.ndjson"
    lines_path.write_text("\n".join(json.dumps(row) for row in records), encoding="utf-8")
    frame, report = load_capture(lines_path)
    assert len(frame) == 25


def test_xml(sandbox):
    rows = []
    for index in range(25):
        fields = "".join(
            f"<{name}>{value}</{name}>"
            for name, value in zip(CANONICAL_HEADER, a_row(index)))
        rows.append(f"<transaction>{fields}</transaction>")
    path = sandbox / "capture.xml"
    path.write_text(f"<captures>{''.join(rows)}</captures>", encoding="utf-8")
    frame, report = load_capture(path)
    assert len(frame) == 25
    assert report.reader == "xml"
    assert frame["output_amounts"].iloc[0][0] == pytest.approx(1.5)


def test_sqlite(sandbox):
    path = sandbox / "capture.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE transactions (timestamp TEXT, txid TEXT, input_addresses TEXT, "
        "output_addresses TEXT, input_amounts TEXT, output_amounts TEXT)")
    for index in range(25):
        connection.execute("INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?)",
                           (f"2026-08-11T0{index % 10}:00:00Z", f"t{index}",
                            f"a{index}", f"b{index}", "1.5", "1.5"))
    connection.commit()
    connection.close()

    frame, report = load_capture(path)
    assert len(frame) == 25
    assert report.reader == "sqlite"


def test_a_tsv_and_a_semicolon_file(sandbox):
    tsv = sandbox / "capture.tsv"
    tsv.write_text("\n".join(
        ["\t".join(CANONICAL_HEADER)] + ["\t".join(a_row(i)) for i in range(25)]),
        encoding="utf-8")
    frame, _report = load_capture(tsv)
    assert len(frame) == 25

    semicolon = sandbox / "capture_semicolon.csv"
    semicolon.write_text("\n".join(
        [";".join(CANONICAL_HEADER)] + [";".join(a_row(i)) for i in range(25)]),
        encoding="utf-8")
    frame, _report = load_capture(semicolon)
    assert len(frame) == 25


# --------------------------------------------------------------------------
# Nothing is invented
# --------------------------------------------------------------------------
def test_absent_network_columns_produce_no_control_edges(sandbox):
    """A capture with no IP data has no network layer. Filling the gap produced an
    endpoint that "controlled" every wallet in the capture -- infrastructure
    asserted out of a column the file never had."""
    path = write_csv(
        sandbox / "chain_only.csv",
        [[f"2026-08-11T0{i % 10}:00:00Z", f"t{i}", f"a{i}", f"b{i}", "1.5", "1.5"]
         for i in range(40)],
        header=["timestamp", "txid", "input_addresses", "output_addresses",
                "input_amounts", "output_amounts"])
    frame, report = load_capture(path)
    absent = {entry.canonical for entry in report.mappings if entry.action == "absent"}
    assert {"src_ip", "dst_ip", "geo_country", "asn"} <= absent
    assert (frame["src_ip"] == "").all()

    from netra.correlate.fuse import correlate

    correlation = correlate(frame)
    assert correlation.controls.empty, "control edges were invented"
    assert correlation.entities["country_count"].max() == 0


def test_the_report_says_what_the_analysis_loses(sandbox):
    """Every absent field comes with what it costs, and the note reaches the
    operator before anything expensive runs."""
    path = write_csv(
        sandbox / "chain_only2.csv",
        [[f"2026-08-11T0{i % 10}:00:00Z", f"t{i}", f"a{i}", f"b{i}", "1.5", "1.5"]
         for i in range(40)],
        header=["timestamp", "txid", "input_addresses", "output_addresses",
                "input_amounts", "output_amounts"])
    _frame, report = load_capture(path)
    missing = [entry for entry in report.mappings if entry.action == "absent"]
    assert missing, "nothing was reported as absent"
    assert all(entry.note for entry in missing), "an absent field with no stated cost"
    assert any("geography" in entry.note for entry in missing)


def test_the_report_serialises_for_the_interface(sandbox):
    """The ingest screen renders `report.as_dict()` field for field, so the shape
    is part of the contract with the page."""
    path = write_csv(sandbox / "report.csv", [a_row(i) for i in range(25)])
    _frame, report = load_capture(path)
    as_dict = report.as_dict()
    for key in ("source", "reader", "rows_read", "rows_usable", "rows_rejected",
                "rejections", "mapping", "span"):
        assert key in as_dict, key
    assert as_dict["reader"] == report.reader
    assert isinstance(as_dict["mapping"], list)


def test_the_same_capture_reads_the_same_way_twice(sandbox):
    """Reproducibility: the same file must produce the same frame and the same
    report, or a report cannot be compared with the run that produced it."""
    path = write_csv(sandbox / "twice.csv", [a_row(i) for i in range(30)])
    first, first_report = load_capture(path)
    second, second_report = load_capture(path)
    pd.testing.assert_frame_equal(first, second)
    assert first_report.as_dict() == second_report.as_dict()
