"""
The feedback loop: analyst decisions becoming a model, and never becoming a model
that promoted itself.

The interesting assertions here are about what the loop REFUSES to do -- train on
four labels, overwrite a champion, or let a disposition outrank a confirmed
outcome -- because those are the failures that would look like success.
"""

from __future__ import annotations

import json

import pytest

from netra import config
from netra.models.feedback import MIN_LABELS, labels_from_dispositions, retrain_from_labels
from netra.state.product import ProductStore

# Each test names its own fixture. A module-level `sandbox` mark would fight the
# `workspace` fixture -- both redirect config, and whichever pytest builds last
# wins -- so the store-only tests take `sandbox` and the model tests take
# `workspace`.


def a_store():
    return ProductStore(config.STATE_DB)


def decide(entity_key: str, label: str, *, source: str = "disposition",
           window_id: int | None = None) -> None:
    with a_store() as store:
        store.add_disposition(entity_key, label, window_id=window_id, source=source,
                              actor="analyst1", note="from the queue")


# --------------------------------------------------------------------------
# What a decision means
# --------------------------------------------------------------------------
def test_decisions_become_labels_with_their_strength(sandbox):
    decide("NTR-0001", "closed_escalated")
    decide("NTR-0002", "closed_false_positive")
    decide("NTR-0003", "confirmed", source="confirmed")

    labels = labels_from_dispositions()
    assert labels["NTR-0001"]["label"] == 1
    assert labels["NTR-0002"]["label"] == 0
    assert labels["NTR-0001"]["strength"] < labels["NTR-0003"]["strength"], (
        "a confirmed outcome must weigh more than a judgement made in minutes")


def test_a_note_is_not_a_decision(sandbox):
    """`acknowledged` and a free-text note are not labels: treating them as labels
    would teach the model from the act of looking at something."""
    decide("NTR-0001", "acknowledged")
    decide("NTR-0002", "investigating")
    decide("NTR-0003", "closed_escalated")
    labels = labels_from_dispositions()
    assert set(labels) == {"NTR-0003"}


def test_a_later_decision_replaces_an_earlier_one(sandbox):
    decide("NTR-0001", "closed_false_positive")
    decide("NTR-0001", "closed_escalated")
    assert labels_from_dispositions()["NTR-0001"]["label"] == 1


def test_evidence_outranks_a_later_judgement(sandbox):
    """A confirmed outcome is not overwritten by somebody's second opinion: the
    model is being trained on what happened, and a disposition is what was thought
    at the time."""
    decide("NTR-0001", "confirmed", source="confirmed")
    decide("NTR-0001", "closed_false_positive")
    labels = labels_from_dispositions()
    assert labels["NTR-0001"]["label"] == 1
    assert labels["NTR-0001"]["source"] == "confirmed"


def test_no_store_means_no_labels(sandbox):
    assert labels_from_dispositions(sandbox / "state" / "not-here.sqlite") == {}


# --------------------------------------------------------------------------
# What the retrain refuses to do
# --------------------------------------------------------------------------
def test_it_refuses_to_train_on_a_handful_of_labels(sandbox):
    for index in range(MIN_LABELS - 1):
        decide(f"NTR-{index:04d}", "closed_escalated")
    result = retrain_from_labels(data_dir=sandbox / "data",
                                 models_dir=sandbox / "models",
                                 store_path=config.STATE_DB)
    assert result["trained"] is False
    assert result["reason"] == "too few labels"
    assert not (sandbox / "models" / "challengers").exists(), (
        "a refused retrain must not leave an artifact behind")


def test_it_refuses_when_the_decisions_leave_one_class(workspace, monkeypatch):
    """A training set with one class is not a training set, it is a list.

    Reached by making the capture itself single-class rather than by recording a
    lot of positive decisions: the planted labels still supply the negative class,
    and a test that pretended otherwise would be asserting something the code does
    not claim.
    """
    import numpy as np

    from netra.models import train as training
    from netra.state.store import MonitoringStore

    with MonitoringStore(config.STATE_DB) as analysis:
        keys = sorted(set(analysis.pinned_keys().values()))[:MIN_LABELS + 4]
    for key in keys:
        decide(key, "closed_escalated")

    real = training.build_windowed_training_data

    def one_class(data_dir, prefix="train-window"):
        data = real(data_dir, prefix=prefix)
        data["labels"] = np.ones_like(np.asarray(data["labels"], dtype=int))
        return data

    monkeypatch.setattr(training, "build_windowed_training_data", one_class)
    result = retrain_from_labels(data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                                 store_path=config.STATE_DB)
    assert result["trained"] is False
    assert result["reason"] == "one class"


# --------------------------------------------------------------------------
# What it does when it trains
# --------------------------------------------------------------------------
@pytest.fixture
def a_workspace_with_decisions(workspace):
    """The shared workspace, with decisions recorded on both classes."""
    from netra.state.store import MonitoringStore

    with MonitoringStore(config.STATE_DB) as analysis:
        keys = sorted(set(analysis.pinned_keys().values()))
    assert len(keys) >= MIN_LABELS * 2, f"only {len(keys)} entities to decide on"
    for key in keys[:MIN_LABELS]:
        decide(key, "closed_escalated")
    for key in keys[MIN_LABELS:MIN_LABELS * 2]:
        decide(key, "closed_false_positive", source="confirmed")
    return workspace


def test_a_trained_challenger_is_registered_and_not_promoted(a_workspace_with_decisions):
    result = retrain_from_labels(data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                                 store_path=config.STATE_DB)
    assert result["trained"] is True
    version = result["version"]
    assert version, "a trained challenger must be registered"

    with a_store() as store:
        models = {model["version"]: model for model in store.models()}
        assert version in models
        assert models[version]["state"] == "challenger"
        # The live model is unchanged: the point of a challenger is that a person
        # decides when it replaces the champion.
        champion = store.champion()
    assert champion is None or champion["version"] != version


def test_the_challenger_artifact_does_not_overwrite_the_champion(
        a_workspace_with_decisions):
    """Writing the challenger over `models/risk.joblib` would switch the live model
    as a side effect of training one -- the exact thing the registry exists to
    prevent."""
    live = (config.MODELS_DIR / "risk.joblib").read_bytes()
    retrain_from_labels(data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                        store_path=config.STATE_DB)
    assert (config.MODELS_DIR / "risk.joblib").read_bytes() == live
    assert (config.MODELS_DIR / "challengers" / "risk.joblib").exists()


def test_promoting_the_challenger_and_rolling_back(a_workspace_with_decisions):
    """Promotion and rollback are the same call, so neither can behave differently
    from the other."""
    retrain_from_labels(data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                        store_path=config.STATE_DB)
    with a_store() as store:
        store.register_model("netra-3.0-baseline", algorithm="RandomForest",
                             artifact_hash="deadbeef", trained_on="the reference capture",
                             n_labels=100, metrics={"roc_auc": 0.9}, state="champion")
        assert store.champion()["version"] == "netra-3.0-baseline"

        challenger = next(model["version"] for model in store.models()
                          if model["state"] == "challenger")
        store.promote_model(challenger, "admin")
        assert store.champion()["version"] == challenger

        store.promote_model("netra-3.0-baseline", "admin")
        assert store.champion()["version"] == "netra-3.0-baseline"


def test_the_challenger_records_the_decisions_it_learned_from(a_workspace_with_decisions):
    result = retrain_from_labels(data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                                 store_path=config.STATE_DB)
    metrics = json.loads((config.MODELS_DIR / "challengers" / "metrics.json").read_text(
        encoding="utf-8"))
    summary = metrics["analyst_labels"]
    assert summary["decided"] >= MIN_LABELS * 2
    assert summary["confirmed"] >= MIN_LABELS
    assert metrics["training_mode"] == "challenger trained with analyst labels"


def test_analyst_labels_can_overrule_the_planted_ones(workspace):
    """Where the analyst and the generator disagree, the analyst is right: the tool
    is being asked to reproduce the investigator's judgement, not the generator's."""
    from netra.models.feedback import training_rows_with_labels
    from netra.models.train import build_windowed_training_data

    data = build_windowed_training_data(config.DATA_DIR)
    planted = list(data["labels"])
    entity_ids = list(data["table"]["entity_id"])

    # Pick a wallet the generator calls clean and decide it is a real operation.
    clean = next(entity for entity, label in zip(entity_ids, planted) if label == 0)
    decisions = {clean: {"label": 1, "strength": 0.5, "source": "disposition",
                         "window": None, "at": "now"}}
    _matrix, labels, summary = training_rows_with_labels(data, decisions)

    # Every row that entity has, in every batch it appears in. Labelling only one
    # of them would leave the model being told two contradictory things about the
    # same wallet.
    rows = [index for index, entity in enumerate(entity_ids) if entity == clean]
    assert rows, "the chosen entity is not in the training table"
    assert all(labels[index] == 1 for index in rows)
    assert summary["flipped"] == 1
    assert summary["rows"] == len(rows)
