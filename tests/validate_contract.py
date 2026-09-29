"""
Validating the payload against the frozen contract, and the invariants the
contract cannot express.

WHY BOTH
--------
`schemas/netra.schema.json` states the SHAPE: which keys exist, their types, the
patterns the ids follow. It cannot state the things that actually make the numbers
trustworthy -- that the listed contributions add up to the score, that an entity id
never changes its meaning, that a control edge is not counted as a money movement.
Those are checked here, in code, and each one exists because a version of this
engine got it wrong.

WHAT IS VALIDATED, AND WHEN
---------------------------
The payload is validated ON THE WAY OUT: `netra/api/app.py` calls `validate_payload`
on the JSON it is about to serve, and a payload that breaks the contract is a 500
rather than a page that quietly renders the wrong thing. `tests/test_contract.py`
validates against a store built from scratch, so the contract is checked in CI too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netra import config                                   # noqa: E402

# How far the attribution may miss before it is a defect rather than rounding.
# The payload rounds each contribution to 6 dp; 33 of them rounded at 6 dp cannot
# drift further than this.
ATTRIBUTION_TOLERANCE = 1e-4


def load_schema(path: Path | str | None = None) -> dict[str, Any]:
    """The contract, from disk. Missing is an error, not a pass: this project's
    first rule is that an output says which contract shaped it."""
    schema_path = Path(path or config.SCHEMA_PATH)
    if not schema_path.exists():
        raise FileNotFoundError(
            f"the contract schema is missing: {schema_path}. The engine cannot "
            "claim to honour a contract whose own text is not on the machine.")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_payload(payload: dict[str, Any]) -> list[str]:
    """Every way this payload breaks the contract, as readable sentences.

    An empty list means it is conformant. The list is returned rather than raised
    so the CLI can print all the problems at once: fixing a contract break one
    round-trip at a time is how people give up and widen the schema instead.
    """
    problems: list[str] = []

    try:
        from jsonschema import Draft7Validator
    except ImportError:                                     # pragma: no cover
        return ["jsonschema is not installed: the contract cannot be checked"]

    schema = load_schema()
    validator = Draft7Validator(schema)
    for error in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        where = "/".join(str(part) for part in error.path) or "(root)"
        problems.append(f"schema: {where}: {error.message}")

    problems.extend(_invariants(payload))
    return problems


def _invariants(payload: dict[str, Any]) -> list[str]:
    """The rules the schema cannot state. Each one is a defect this engine has had."""
    problems: list[str] = []
    entities = payload.get("entities") or []
    ids = {entity.get("id") for entity in entities}

    # --- the version travels with the payload ------------------------------
    meta = payload.get("meta") or {}
    if meta.get("schema_version") != config.SCHEMA_VERSION:
        problems.append(
            f"meta/schema_version is {meta.get('schema_version')!r} but this engine "
            f"speaks {config.SCHEMA_VERSION!r}: a report would cite a contract that "
            "did not produce it")
    if config.ENGINE_NAME not in str(meta.get("engine_version", "")):
        problems.append(f"meta/engine_version {meta.get('engine_version')!r} does not "
                        f"name this engine ({config.ENGINE_NAME!r})")

    # --- identity -----------------------------------------------------------
    for entity in entities:
        key = str(entity.get("id", ""))
        if key.startswith("NTR-") and entity.get("kind") == "ip":
            problems.append(f"entity {key} is an endpoint but carries a wallet-group id")
        if key.startswith("IP-") and entity.get("kind") != "ip":
            problems.append(f"entity {key} is a wallet group but carries an endpoint id")

    # --- the attribution has to reconcile -----------------------------------
    # The interface prints base + named + smaller = score. If that is not exactly
    # true the screen is asserting arithmetic that does not hold, and the one
    # number an investigator is asked to trust becomes unverifiable.
    for entity in entities:
        explanation = entity.get("explanation")
        if not explanation:
            continue
        listed = explanation.get("listed_count")
        features = entity.get("features") or []
        if listed is not None and listed != len(features):
            problems.append(
                f"{entity.get('id')}: explanation claims {listed} listed contributions "
                f"but {len(features)} were published")
        named = sum(float(item.get("importance") or 0.0) for item in features)
        total = (float(explanation.get("base") or 0.0) + named
                 + float(explanation.get("other_contribution") or 0.0))
        gap = abs(total - float(explanation.get("prediction") or 0.0))
        if gap > ATTRIBUTION_TOLERANCE:
            problems.append(
                f"{entity.get('id')}: attribution does not reconcile -- base + named + "
                f"other = {total:.6f} against a stated {explanation.get('prediction')}, "
                f"a gap of {gap:.2e}")

    # --- ranking -------------------------------------------------------------
    # The queue is ordered by the model's probability. The anomaly score is a
    # different model's opinion and must never order it: it did once, and the
    # queue then disagreed with the number printed in its own Priority column.
    leads = [entity for entity in entities if entity.get("kind") != "ip"]
    risks = [int(entity.get("risk") or 0) for entity in leads]
    if risks != sorted(risks, reverse=True):
        problems.append("entities are not ordered by risk, which is the order the "
                        "queue claims to show")
    for entity in leads:
        confidence = entity.get("confidence")
        if confidence is None:
            continue
        expected = int(round(float(confidence) * 100))
        if abs(expected - int(entity.get("risk") or 0)) > 1:
            problems.append(
                f"{entity.get('id')}: confidence {confidence} does not correspond to a "
                f"risk of {entity.get('risk')} -- the two are the same number, rounded "
                "and unrounded")

    # --- edges ---------------------------------------------------------------
    for index, edge in enumerate(payload.get("edges") or []):
        if edge.get("kind") == "flow" and edge.get("value") is None:
            problems.append(f"edge {index}: a flow edge with no value")
        for side in ("from", "to"):
            if ids and edge.get(side) not in ids:
                problems.append(
                    f"edge {index}: {side}={edge.get(side)!r} is not an entity in this "
                    "payload, so the map would draw a link to a node it never shows")

    # --- campaigns -----------------------------------------------------------
    for campaign in payload.get("campaigns") or []:
        victims = campaign.get("victims")
        count = campaign.get("victim_count")
        if victims is not None and count is not None and len(victims) != int(count):
            problems.append(
                f"campaign {campaign.get('id')}: victim_count {count} does not match the "
                f"{len(victims)} victims listed")

    # --- traces --------------------------------------------------------------
    for trace in payload.get("traces") or []:
        for sink in trace.get("sinks") or []:
            path = sink.get("path")
            if path and sink.get("entity") and path[-1] != sink.get("entity"):
                problems.append(
                    f"trace {trace.get('seed')}: a sink named {sink.get('entity')} whose "
                    f"path ends at {path[-1]}")
            if path and len(path) - 1 != int(sink.get("hops") or 0):
                problems.append(
                    f"trace {trace.get('seed')}: a sink at {len(path) - 1} hops recorded "
                    f"as {sink.get('hops')}")

    return problems


def validate_file(path: Path | str) -> list[str]:
    """Validate a payload on disk. Used by `tasks.py payload`."""
    return validate_payload(json.loads(Path(path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else str(config.STATE_DIR / "window-0.json")
    found = validate_file(target)
    if found:
        print(f"{target}: {len(found)} problem(s)")
        for item in found:
            print(f"  - {item}")
        raise SystemExit(1)
    print(f"{target}: conforms to the contract (schema {config.SCHEMA_VERSION})")
