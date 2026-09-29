"""
What a capture IS, and how a file that was not written for us becomes one.

THE PROBLEM THIS SOLVES
-----------------------
The product will be handed real files: an export from a traffic capture tool, a
chain extract from a node, a spreadsheet someone keeps. None of them will have our
column names, and some will be missing fields entirely.

There are two bad ways to handle that. The first is to demand an exact schema, so
the tool rejects the customer's own data. The second is to guess silently, so the
tool reads `amount` as BTC when the file meant satoshis and reports a confident
wrong answer.

So this module does the third thing: it maps what it recognises, states every
mapping it made, and refuses rows it cannot interpret *with the reason*. The
mapping report is part of the product, not a log line -- an analyst who has just
uploaded their first file needs to see that we read their `from_addr` column and
noticed their timestamps had no timezone.

THE CANONICAL FIELDS
--------------------
Required: a timestamp, and enough address information to build a graph.
Everything else materially improves the analysis and is reported when absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# The canonical capture. Order matters only for the CSV header we emit.
# --------------------------------------------------------------------------
CANONICAL_FIELDS: tuple[str, ...] = (
    "timestamp",
    "txid",
    "src_ip",
    "dst_ip",
    "src_port",
    "dst_port",
    "input_addresses",
    "output_addresses",
    "input_amounts",
    "output_amounts",
    "geo_country",
    "asn",
)

REQUIRED_FIELDS: tuple[str, ...] = ("timestamp",)
# A capture with neither side of a transfer cannot produce a graph; with only one
# side it can still be analysed, and the report says which side was missing.
TRANSFER_FIELDS: tuple[str, ...] = ("input_addresses", "output_addresses")

# --------------------------------------------------------------------------
# Column aliases. Lower-cased and stripped before lookup, because a real header
# might be `From Address`, `from_address` or `FROM_ADDR`.
#
# This table is a product feature, not a convenience: it is the difference between
# "your file was rejected" and "we read it". Every entry is a name seen in an
# actual export format (block explorers, node RPC dumps, CSV exports from capture
# tooling, and spreadsheets).
# --------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": (
        "timestamp", "time", "datetime", "date_time", "date", "ts", "seen_at",
        "first_seen", "block_time", "created_at", "observed_at",
    ),
    "txid": (
        "txid", "tx_id", "hash", "transaction_hash", "txhash", "transaction_id", "id",
    ),
    "src_ip": (
        "src_ip", "source_ip", "srcaddr", "src_addr", "srcaddr", "source", "ip_src",
        "from_ip", "srcaddress", "source_address",
    ),
    "dst_ip": (
        "dst_ip", "dest_ip", "destination_ip", "dstaddr", "dst_addr", "dest",
        "ip_dst", "to_ip", "dstaddress", "destination_address", "peer_ip",
    ),
    "src_port": ("src_port", "source_port", "sport", "srcport", "from_port"),
    "dst_port": (
        "dst_port", "dest_port", "destination_port", "dport", "dstport", "to_port",
    ),
    "input_addresses": (
        "input_addresses", "inputs", "input_addr", "in_addresses", "from_address",
        "from_addresses", "sender", "sender_address", "source_addresses",
        "in_addrs", "senders", "from_addr", "from",
    ),
    "output_addresses": (
        "output_addresses", "outputs", "output_addr", "out_addresses",
        "to_address", "to_addresses", "recipient", "recipient_address",
        "destination_addresses", "out_addrs", "receivers", "to_addr", "to",
    ),
    "input_amounts": (
        "input_amounts", "input_amount", "in_amounts", "in_amount",
        "amount_in", "value_in", "from_amount",
    ),
    "output_amounts": (
        "output_amounts", "output_amount", "out_amounts", "out_amount",
        "amount_out", "value_out", "to_amount",
    ),
    # A single `amount`/`value` column is extremely common and is ambiguous: it is
    # the transaction's value, which we record as BOTH the input and output side
    # for that row. Recording it in one direction only would halve the flow.
    "amount": ("amount", "value", "amount_btc", "value_btc", "btc", "sum", "total"),
    "geo_country": (
        "geo_country", "country", "country_code", "geo", "country_iso",
        "src_country", "cc",
    ),
    "asn": ("asn", "as_name", "asnumber", "as", "network", "org", "isp", "asn_org"),
}

# Suggested canonical fields a file may omit, with what the tool assumes and what
# is lost by assuming it.
DEFAULTS_AND_CONSEQUENCES: dict[str, str] = {
    "txid": "no transaction id: rows are counted individually, so a duplicate "
            "transfer cannot be detected",
    "src_ip": "no source network endpoint: the wallet groups in this capture have "
              "no country or hosting operator attributed to them",
    "dst_ip": "no destination network endpoint: control edges are one-sided",
    "src_port": "no source port: the network layer is thinner but usable",
    "dst_port": "no destination port: protocol identification is unavailable",
    "input_amounts": "no input amounts: transaction value is taken from the output "
                     "side, and inflow totals are approximate",
    "output_amounts": "no output amounts: transaction value is taken from the input "
                      "side, and outflow totals are approximate",
    "geo_country": "no country column: wallet groups get no geography",
    "asn": "no operator column: hosting attribution is unavailable",
}

# A value that means "we do not have this", as opposed to a real value. Kept as a
# set because real exports use all of them, and a blank treated as data is worse
# than a blank treated as blank -- "N/A" parsed as a country becomes a country.
PLACEHOLDERS: frozenset[str] = frozenset({
    "", "-", "--", "n/a", "na", "nan", "null", "none", "nil", "unknown", "?",
    "undefined", "not available", "not applicable", "#n/a", "#value!",
})


@dataclass
class ColumnMapping:
    """One canonical field, and where it came from."""

    canonical: str
    source: str | None
    action: str          # "mapped" | "derived" | "absent" | "ignored"
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.canonical,
            "from": self.source,
            "action": self.action,
            "note": self.note,
        }


@dataclass
class IngestReport:
    """What we made of the file.

    Every figure here is shown to the operator *before* anything expensive runs,
    because the question they are asking is "did you actually use my data?" and an
    answer that arrives after a two-minute analysis is not an answer.
    """

    source: str
    reader: str                                   # csv | json | xml | sqlite
    total: int = 0
    accepted: int = 0
    rejected: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    notes: dict[str, int] = field(default_factory=dict)
    mappings: list[ColumnMapping] = field(default_factory=list)
    timezone_assumed: str | None = None
    span_start: str | None = None
    span_end: str | None = None
    amount_unit_note: str = ""

    def reject(self, reason: str, count: int = 1) -> None:
        self.rejections[reason] = self.rejections.get(reason, 0) + count

    def note(self, text: str, count: int = 1) -> None:
        self.notes[text] = self.notes.get(text, 0) + count

    @property
    def usable_share(self) -> float:
        return self.accepted / self.total if self.total else 0.0

    @property
    def format(self) -> str:
        """The reader that produced this capture. Named `format` because that is
        what the payload contract publishes, and the report is the only place the
        answer exists."""
        return self.reader

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reader": self.reader,
            "rows_read": self.total,
            "rows_usable": self.accepted,
            "rows_rejected": self.rejected,
            "rejections": dict(sorted(self.rejections.items(), key=lambda kv: -kv[1])),
            "notes": dict(sorted(self.notes.items(), key=lambda kv: -kv[1])),
            "mapping": [entry.as_dict() for entry in self.mappings],
            "timezone_assumed": self.timezone_assumed,
            "span": {"start": self.span_start, "end": self.span_end},
            "amount_unit_note": self.amount_unit_note,
        }


def normalise_header(name: Any) -> str:
    """Fold a header to the form the alias table is keyed on."""
    return str(name).strip().lower().replace("-", "_").replace(" ", "_")


def is_placeholder(value: Any) -> bool:
    """Is this cell empty, or a way of writing empty?"""
    if value is None:
        return True
    text = str(value).strip().lower()
    return text in PLACEHOLDERS


def map_columns(headers: list[str]) -> tuple[dict[str, str], list[ColumnMapping]]:
    """Work out which of the file's columns is which of ours.

    Returns `{canonical: source_column}` plus the mapping record. A source column
    that matches nothing is recorded as ignored rather than silently dropped --
    "we did not use your `note` column" is information the operator is entitled to.
    """
    folded = {normalise_header(header): header for header in headers}
    taken: set[str] = set()
    resolved: dict[str, str] = {}
    mappings: list[ColumnMapping] = []

    # Exact alias matches first, so an explicit `from_address` always beats a
    # looser rule. Then the ambiguous single-amount column, which is handled
    # separately because one source feeds two canonical fields.
    for canonical, aliases in COLUMN_ALIASES.items():
        if canonical == "amount":
            continue
        for alias in aliases:
            if alias in folded and folded[alias] not in taken:
                resolved[canonical] = folded[alias]
                taken.add(folded[alias])
                mappings.append(ColumnMapping(canonical, folded[alias], "mapped"))
                break

    amount_source = None
    for alias in COLUMN_ALIASES["amount"]:
        if alias in folded and folded[alias] not in taken:
            amount_source = folded[alias]
            taken.add(amount_source)
            break

    if amount_source:
        # One column, two directions. Recorded as derived so the report can say so
        # rather than looking like two independent readings of the file.
        for canonical in ("input_amounts", "output_amounts"):
            if canonical not in resolved:
                resolved[canonical] = amount_source
                mappings.append(ColumnMapping(
                    canonical, amount_source, "derived",
                    "single amount column used for both directions"))

    for canonical in CANONICAL_FIELDS:
        if canonical in resolved:
            continue
        if canonical in TRANSFER_FIELDS:
            mappings.append(ColumnMapping(
                canonical, None, "absent",
                "no transfer side found; the graph will be one-sided"))
            continue
        mappings.append(ColumnMapping(
            canonical, None, "absent", DEFAULTS_AND_CONSEQUENCES.get(canonical, "not present")))

    for header in headers:
        if header not in taken:
            mappings.append(ColumnMapping(header, header, "ignored",
                                          "not a field this analysis uses"))

    return resolved, mappings
