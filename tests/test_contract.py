"""
The contract test: the payload the engine publishes against the schema it claims.

This is the test that stops the four consumers of the payload -- the workstation,
the printed reports, the exports and the API -- from drifting apart one field at a
time. It validates a payload built from a store created for the test, plus each
individual batch, and it checks the invariants the schema cannot express.
"""

from __future__ import annotations

import json

import pytest

from netra import config
from netra.operations.payload import payload_for
from tests.validate_contract import load_schema, validate_payload


def test_schema_is_present_and_valid():
    """A contract whose own text is missing cannot be honoured, only claimed."""
    from jsonschema import Draft7Validator

    schema = load_schema()
    Draft7Validator.check_schema(schema)
    assert schema["title"] == "NETRA analysis payload"


def test_union_payload_conforms(analysis_store):
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    assert validate_payload(payload) == []


def test_every_batch_payload_conforms(analysis_store):
    """Every batch, not just the one the demo opens on."""
    windows = analysis_store.windows()
    assert windows, "the fixture produced no batches"
    for record in windows:
        payload = payload_for(analysis_store, str(record.window_id), default="latest",
                              models_dir=config.MODELS_DIR)
        problems = validate_payload(payload)
        assert problems == [], f"batch {record.window_id}: {problems}"


def test_payload_names_the_capture_and_the_contract(analysis_store):
    """A report has to be able to say which file it is about and which contract
    shaped it. It said "synthetic_v1" for an uploaded capture until this existed."""
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    meta = payload["meta"]
    assert meta["schema_version"] == config.SCHEMA_VERSION
    assert config.ENGINE_NAME in meta["engine_version"]
    assert meta["dataset"].endswith(".csv"), meta["dataset"]


def test_attribution_reconciles_on_every_lead(analysis_store):
    """base + named + smaller == the score on screen, exactly.

    The interface prints that sum under the bars. If it does not hold, the one
    number an investigator is asked to trust is unverifiable.
    """
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    checked = 0
    for entity in payload["entities"]:
        explanation = entity.get("explanation")
        if not explanation:
            continue
        named = sum(float(item["importance"]) for item in (entity.get("features") or []))
        total = (float(explanation["base"]) + named
                 + float(explanation["other_contribution"]))
        assert abs(total - float(explanation["prediction"])) < 1e-4, entity["id"]
        checked += 1
    assert checked, "no entity carried an attribution, so nothing was checked"


def test_queue_order_is_the_published_score_order(analysis_store):
    """Ranked by risk, then by the model's own probability, never by the anomaly
    score: the anomaly detector's precision@20 is 0.05 against a 0.0916 base rate
    and it is not allowed to decide the order anywhere in this system."""
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    leads = [entity for entity in payload["entities"] if entity["kind"] != "ip"]
    order = [(-int(entity["risk"]), -float(entity["confidence"]), entity["id"])
             for entity in leads]
    assert order == sorted(order), "the payload is not in the order the queue claims"


def test_confidence_is_the_same_number_as_the_risk(analysis_store):
    """`risk` is the probability x 100 and `confidence` is that probability
    unrounded, so they must round to each other. They diverged by 10.5 points once,
    because the explanation was rebuilt from a different feature vector than the
    score: a merge decided in a later batch changes an earlier batch's graph."""
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    for entity in payload["entities"]:
        if entity["kind"] == "ip":
            continue
        expected = int(round(float(entity["confidence"]) * 100))
        assert abs(expected - int(entity["risk"])) <= 1, (
            f"{entity['id']}: risk {entity['risk']} against confidence "
            f"{entity['confidence']}")


def test_endpoints_are_never_leads(analysis_store):
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    for entity in payload["entities"]:
        if entity["kind"] == "ip":
            assert entity["lead"] is False
            assert "confidence" not in entity, (
                "an endpoint has no confidence: the model never scored it")


def test_edges_point_at_entities_in_the_payload(analysis_store):
    """A link to a node the payload never shows is a link the map cannot draw."""
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    ids = {entity["id"] for entity in payload["entities"]}
    for edge in payload["edges"]:
        assert edge["from"] in ids and edge["to"] in ids, edge
        if edge["kind"] == "flow":
            assert edge["value"] is not None


def test_contract_refuses_a_broken_payload():
    """The validator has to reject things, or it is decoration."""
    payload = {"meta": {}, "window": {"id": 0, "label": "x"}, "entities": [], "edges": []}
    problems = validate_payload(payload)
    assert any("schema_version" in item for item in problems)
    assert any("required" in item for item in problems)


def test_contract_catches_an_unreconciled_attribution():
    payload = {
        "meta": {"records": 0, "transactions": 0, "entities": 1, "total_value_btc": 0.0,
                 "generated_at": "2026-01-01T00:00:00+00:00", "dataset": "x.csv",
                 "engine_version": f"{config.ENGINE_NAME}-{config.ENGINE_VERSION}",
                 "schema_version": config.SCHEMA_VERSION},
        "window": {"id": 0, "label": "all"},
        "corpus": {},
        "edges": [],
        "entities": [{
            "id": "NTR-0001", "label": "x", "kind": "wallet", "risk": 80,
            "risk_band": "high", "confidence": 0.8, "typology": [], "reasons": [],
            "explanation": {"method": "m", "base": 0.5, "prediction": 0.8,
                            "residual": 0.0, "listed_count": 1, "other_count": 0,
                            "other_contribution": 0.0},
            "features": [{"name": "a", "value": 1.0, "importance": 0.1}],
        }],
    }
    problems = validate_payload(payload)
    assert any("does not reconcile" in item for item in problems), problems


def test_saved_payload_is_the_validated_payload(analysis_store, tmp_path):
    """What is cached is what was checked: a payload written before validation, or
    after it with a different body, is how a report comes to disagree with the
    screen it was printed from."""
    payload = payload_for(analysis_store, "all", default="all",
                          models_dir=config.MODELS_DIR)
    cache = config.STATE_DIR / "window-0.json"
    assert cache.exists()
    assert json.loads(cache.read_text(encoding="utf-8"))["meta"] == payload["meta"]
