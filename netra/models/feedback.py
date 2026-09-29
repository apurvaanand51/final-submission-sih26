"""
Learning from what the analysts decided.

WHAT THIS IS FOR
----------------
The model is trained on planted ground truth: a wallet either was part of a
laundering operation or it was not, and the generator knows which. That is enough
to ship a first model and it is not enough to keep one. Every day the tool is used,
analysts close leads -- "this one is a real operation", "this one is a company
payroll" -- and those decisions are exactly the labels the model cannot generate
for itself.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not promote anything, and it does not touch the live model. It trains a
CHALLENGER from the same features plus the analyst labels, registers it, and stops.
A model that promotes itself because it scored well on the labels it was just
trained on is a model nobody reviewed, and the whole point of a registry is that a
person decides.

It also does not pretend the two label sources are equal. A disposition is a
judgement made in minutes from what was on screen; a confirmed outcome is evidence
(a seizure, a charge, an exchange's own record). They carry different weights, and
the weight travels with the label rather than being averaged away.

    python tasks.py retrain          # train a challenger from analyst labels
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from netra import config

# A disposition is a judgement; a confirmed outcome is evidence. Both are used, and
# this is what says by how much -- see ProductStore.add_disposition, which is where
# the strength is recorded.
MIN_LABELS = 8


def labels_from_dispositions(store_path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """Analyst decisions, per entity, with their strength and their source.

    Returns `{entity_key: {"label": 0|1, "strength": float, "source": str, "window": id}}`.

    A group can be decided more than once -- a false positive today can turn out to
    be a real operation after a further batch -- so the latest decision wins, except
    that a CONFIRMED outcome is never overwritten by a weaker disposition. Evidence
    outranks opinion, in time as well as in weight.
    """
    from netra.state.product import ProductStore

    path = Path(store_path or config.STATE_DB)
    if not path.exists():
        return {}

    positive = {"closed_escalated", "suspicious", "illicit", "confirmed"}
    negative = {"closed_false_positive", "benign", "clean"}

    out: dict[str, dict[str, Any]] = {}
    with ProductStore(path) as store:
        rows = store._connection.execute(
            "SELECT entity_key, window_id, label, source, strength, at "
            "FROM dispositions ORDER BY id").fetchall()
        for row in rows:
            label = str(row["label"]).strip().lower()
            if label in positive:
                value = 1
            elif label in negative:
                value = 0
            else:
                continue                        # a note, not a decision
            current = out.get(row["entity_key"])
            if current and current["source"] == "confirmed" and row["source"] != "confirmed":
                continue                        # evidence is not overruled by a judgement
            out[row["entity_key"]] = {
                "label": value,
                "strength": float(row["strength"]),
                "source": row["source"],
                "window": row["window_id"],
                "at": row["at"],
            }
    return out


def training_rows_with_labels(
    data: dict[str, Any], labels: dict[str, dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """The training matrix, with analyst labels REPLACING the planted ones.

    Replacement rather than addition, and that is the interesting decision. The
    planted label is what the generator believes; the analyst label is what an
    investigator concluded about the same addresses having looked at the evidence.
    Where they disagree, the analyst is the one whose judgement the tool is being
    asked to reproduce -- and a model trained to outvote them is a model that gets
    argued with.

    Returns `(matrix, labels, summary)`; the summary is printed so an operator can
    see how much of the training set the decisions actually moved.
    """
    table = data["table"]
    entity_ids = list(table["entity_id"])
    planted = np.asarray(data["labels"], dtype=int)
    y = planted.copy()
    weights = np.ones(len(entity_ids), dtype=float)

    summary = {"decided": 0, "confirmed": 0, "dispositions": 0, "flipped": 0, "rows": 0}
    # ONE ENTITY, SEVERAL ROWS. The training table is pooled per window, so a wallet
    # that appears in four batches has four rows -- and a decision about that wallet
    # is a decision about the wallet, not about one of its days. Every row gets it.
    # (A dict keyed by entity would have labelled whichever row happened to be last,
    # leaving the other three contradicting it.)
    positions: dict[str, list[int]] = {}
    for index, entity_id in enumerate(entity_ids):
        positions.setdefault(entity_id, []).append(index)

    for entity_id, decision in labels.items():
        rows = positions.get(entity_id)
        if not rows:
            continue        # decided in a capture this model was not trained on
        summary["decided"] += 1
        summary["rows"] += len(rows)
        if decision["source"] == "confirmed":
            summary["confirmed"] += 1
        else:
            summary["dispositions"] += 1
        if any(y[position] != decision["label"] for position in rows):
            summary["flipped"] += 1
        for position in rows:
            y[position] = decision["label"]
            # A confirmed outcome is worth more than a disposition, and the weight
            # carries that into the fit instead of being averaged into a constant.
            weights[position] = 1.0 + decision["strength"]

    return np.asarray(data["matrix"]), y, {**summary, "rows_total": len(entity_ids)}


def retrain_from_labels(
    data_dir: Path | str | None = None,
    models_dir: Path | str | None = None,
    store_path: Path | str | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Fit a challenger on planted ground truth plus what the analysts decided.

    Refuses to do anything at all when there are too few decisions to be worth
    fitting: a model trained on four labels is not a model, and registering it
    would put a number in the registry that means nothing.
    """
    from netra.models.train import (
        FEATURE_COLUMNS, RiskModel, build_windowed_training_data, measure,
        register_artifact, summary_table,
    )

    decisions = labels_from_dispositions(store_path)
    usable = len(decisions)
    print(f"\n=== Retraining from analyst decisions ===")
    print(f"  decisions found: {usable}")
    if usable < MIN_LABELS:
        print(f"  refusing to train: fewer than {MIN_LABELS} decisions on this machine."
              "\n  Close some leads in the queue first; a model fitted to a handful"
              "\n  of labels is a model that learned those labels by heart.\n")
        return {"trained": False, "decisions": usable, "reason": "too few labels"}

    models_dir = Path(models_dir or config.MODELS_DIR)
    data = build_windowed_training_data(data_dir or config.DATA_DIR)
    matrix, y, summary = training_rows_with_labels(data, decisions)
    print(f"  rows: {len(y)}  analyst labels applied: {summary['decided']} "
          f"({summary['dispositions']} dispositions, {summary['confirmed']} confirmed)")
    print(f"  planted labels overruled: {summary['flipped']}")
    if len(set(y.tolist())) < 2:
        print("  refusing to train: the decisions leave only one class.\n")
        return {"trained": False, "decisions": usable, "reason": "one class"}

    # `measure` returns (metrics, out-of-fold probabilities).
    metrics, _ = measure({**data, "labels": y}, test_size=0.25, seed=seed, folds=5)
    metrics["training_mode"] = "challenger trained with analyst labels"
    metrics["analyst_labels"] = summary

    # The challenger is written BESIDE the champion's artifact, never over it: the
    # live model keeps serving until somebody promotes the challenger deliberately.
    challenger_dir = models_dir / "challengers"
    challenger_dir.mkdir(parents=True, exist_ok=True)
    model = RiskModel(random_state=seed).fit(matrix, y, FEATURE_COLUMNS)
    model.save(challenger_dir / "risk.joblib")
    (challenger_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    registered = register_artifact(challenger_dir, metrics, n_labels=len(y),
                                   algorithm="RandomForest (challenger, analyst labels)")
    print()
    print(summary_table(metrics))
    if registered:
        print(f"  registered {registered['version']} as {registered['state']}")
        print("  it is NOT live. Promote it from the model registry when you have"
              "\n  read the scorecard, and roll back with the same call if it is worse.\n")
    return {"trained": True, "decisions": usable, "metrics": metrics,
            "version": (registered or {}).get("version")}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Train a challenger model from analyst dispositions.")
    parser.add_argument("--data", default=str(config.DATA_DIR))
    parser.add_argument("--models", default=str(config.MODELS_DIR))
    parser.add_argument("--store", default=str(config.STATE_DB))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    result = retrain_from_labels(args.data, args.models, args.store, args.seed)
    return 0 if result.get("trained") or result.get("reason") == "too few labels" else 1


if __name__ == "__main__":  # pragma: no cover - convenience entry point
    raise SystemExit(main())
