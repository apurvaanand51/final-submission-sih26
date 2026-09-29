"""
The two detectors the synthetic capture never planted.

WHY THEY ARE SEPARATE FROM `patterns.py`
----------------------------------------
`patterns.py` computes the shapes the product has always had — peel chains,
mixing, collectors, exchange behaviour, round amounts. These two are different in
kind: both are *campaign* behaviours rather than single-wallet shapes, and both
were absent from the evaluation because nothing planted them. Keeping them in
their own module makes the new work obvious and lets each carry its own reasoning.

  1 · ADDRESS POISONING
      An adversary sends a near-zero payment from an address generated to look
      like one of the victim's real counterparties — matching the leading and
      trailing characters, because that is all a wallet UI displays when it
      truncates. The victim later copies the wrong address out of their own
      history. The transfer is worthless; the attack is that the address book now
      contains a forgery.

      Two facts we already ingest detect it: the transfer is dust, and the sender's
      address mimics one of the recipient's established counterparties. The
      attacker's forged addresses are co-spent by one actor, so common-input
      clustering already groups them — which is what turns "a wallet received dust"
      into "one cluster baited nine wallets".

  2 · POOL SWEEP (the exit-scam shape)
      Many deposits consolidate into a collection wallet, then the balance leaves
      in one movement shortly afterwards. The EVM form of a rug pull needs contract
      logs and pool state and is a different product; this is the form a chain-only
      view can see, and it is the same shape as an exit scam on a custodial service.

WHAT THESE DELIBERATELY DO NOT DO
---------------------------------
They decide nothing. Each returns scores in [0, 1] plus the counts behind them, and
those numbers enter the feature matrix so the model weighs them — a rule that
decides its own verdict is a rule that quietly becomes the answer. The counts are
kept as well, because the interface has to say *why* in words ("received 3 dust
payments from addresses mimicking its real counterparties") and a bare score
cannot carry that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from netra.correlate.fuse import CorrelationResult

# --------------------------------------------------------------------------
# Thresholds. Constants because each is a judgement, and a judgement buried in an
# expression is a judgement nobody can review.
# --------------------------------------------------------------------------
# A transfer this small has no economic purpose; it exists to be seen later. Well
# under a dollar at any plausible rate.
DUST_ABS_BTC = 0.001
# It is also relative. On a capture whose ordinary payments are themselves tiny an
# absolute cutoff would label everything as dust, so a payment is dust if it is
# tiny in absolute terms OR two orders of magnitude below the recipient's typical
# incoming payment.
DUST_RELATIVE = 0.01
# Leading and trailing characters a wallet UI shows when it truncates -- and
# therefore how many an attacker must match.
LOOKALIKE_CHARS = 4
# The exit must be at least this many times everything that arrived in the window
# before it. Three is deliberately well above ordinary churn: a wallet that pays
# out more than it took in over a day is unusual, one that pays out three times
# that has made a decision.
SWEEP_MIN_CONCENTRATION = 3.0
# The window an exit is judged against.
SWEEP_WINDOW_HOURS = 24.0
# There must have been a real pooling phase, not two payments and an exit.
SWEEP_MIN_DEPOSITS = 5
# The exit must follow the arrivals rather than trailing them by days.
SWEEP_MAX_GAP_HOURS = 48.0


@dataclass
class PoisonEvent:
    """One dust transfer whose sender mimics a real counterparty of the recipient."""

    victim: str
    sender: str
    mimicked: str
    lookalike: str
    amount: float
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "victim": self.victim,
            "sender": self.sender,
            "mimicked": self.mimicked,
            "lookalike": self.lookalike,
            "amount": round(float(self.amount), 10),
            "timestamp": self.timestamp,
        }


@dataclass
class Campaign:
    """One attacker cluster and everyone it baited."""

    cluster: str
    victims: list[str]
    events: list[PoisonEvent]
    dust_total: float
    addresses: list[str]

    def as_dict(self) -> dict[str, Any]:
        digits = "".join(ch for ch in self.cluster if ch.isdigit())[:4] or "0000"
        return {
            "id": f"A-{digits}",
            "cluster": self.cluster,
            "victims": self.victims,
            "victim_count": len(self.victims),
            "dust_total": round(float(self.dust_total), 10),
            "addresses": self.addresses,
            "events": [event.as_dict() for event in self.events],
        }


def _as_list(value: Any) -> list[str]:
    """Split a cell into parts, tolerating every separator in use."""
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    return [part for part in text.replace(";", "|").replace(",", "|").split("|") if part]


def poisoning(corr: CorrelationResult, frame: pd.DataFrame) -> tuple[pd.DataFrame, list[Campaign]]:
    """Lookalike-dust bait, as per-entity features plus the campaign list.

    Both come out of one pass because the campaign view needs the same matching the
    features do, and computing it twice is how a screen starts disagreeing with the
    model about the same wallet.
    """
    empty = pd.DataFrame(columns=["entity_id"])
    if frame.empty or "input_addresses" not in frame.columns:
        return empty, []

    address_of = corr.address_to_entity
    senders_of: dict[str, list[tuple[str, float, str]]] = {}

    for stamp, inputs, outputs, in_amounts, out_amounts in zip(
        frame["timestamp"], frame["input_addresses"], frame["output_addresses"],
        frame["input_amounts"], frame["output_amounts"],
    ):
        senders = _as_list(inputs)
        recipients = _as_list(outputs)
        if not senders or not recipients:
            continue
        value = float(np.sum(_numbers(in_amounts, out_amounts)))
        # Keyed by RECIPIENT ENTITY, not by recipient address.
        #
        # This is not a detail. A wallet group holds several addresses and a
        # payment lands on one of them, so grouping by address shreds the victim's
        # history into one-payment fragments and hides the pattern entirely.
        # Measured on a capture with 19 planted baits: address-keyed detection
        # found 1, entity-keyed found all 19. The address book under attack
        # belongs to the owner, not to one address.
        stamp_text = str(pd.Timestamp(stamp))[:16]
        for recipient in recipients:
            victim = address_of.get(recipient)
            if victim is None:
                continue
            for sender in senders:
                senders_of.setdefault(victim, []).append((sender, value, stamp_text))

    per_victim: dict[str, list[PoisonEvent]] = {}
    attacker_victims: dict[str, set[str]] = {}
    attacker_events: dict[str, list[PoisonEvent]] = {}
    attacker_addresses: dict[str, set[str]] = {}
    attacker_dust: dict[str, float] = {}

    for victim, payments in senders_of.items():
        if len(payments) < 2:
            continue
        amounts = np.array([amount for _, amount, _ in payments], dtype=float)
        positive = amounts[amounts > 0]
        if positive.size == 0:
            continue
        cutoff = max(DUST_ABS_BTC, float(np.median(positive)) * DUST_RELATIVE)
        established = {sender for sender, amount, _ in payments if amount > cutoff}
        dust = [(sender, amount, stamp) for sender, amount, stamp in payments
                if amount <= cutoff]

        for lookalike, amount, stamp in dust:
            head, tail = lookalike[:LOOKALIKE_CHARS], lookalike[-LOOKALIKE_CHARS:]
            mimicked = next(
                (candidate for candidate in established
                 if candidate != lookalike
                 and candidate[:LOOKALIKE_CHARS] == head
                 and candidate[-LOOKALIKE_CHARS:] == tail),
                None,
            )
            # BOTH ends must match. One matching end is a coincidence in a
            # 34-character address; both matching, to the characters a wallet
            # actually displays, is the attack.
            if mimicked is None:
                continue

            sender = address_of.get(lookalike, lookalike)
            event = PoisonEvent(victim=victim, sender=sender, mimicked=mimicked,
                                lookalike=lookalike, amount=amount, timestamp=stamp)
            per_victim.setdefault(victim, []).append(event)
            attacker_victims.setdefault(sender, set()).add(victim)
            attacker_events.setdefault(sender, []).append(event)
            attacker_addresses.setdefault(sender, set()).add(lookalike)
            attacker_dust[sender] = attacker_dust.get(sender, 0.0) + amount

    rows = [{
        "entity_id": entity_id,
        # Victim side: was this wallet baited?
        "dust_received": float(len(per_victim.get(entity_id, []))),
        "lookalike_score": 1.0 if per_victim.get(entity_id) else 0.0,
        # Attacker side: did it bait others? Scale matters -- one forgery may be a
        # mistake, nine is a campaign.
        "dust_campaign_size": float(len(attacker_victims.get(entity_id, set()))),
    } for entity_id in corr.entities["entity_id"]]

    campaigns = [Campaign(cluster=cluster, victims=sorted(victims),
                          events=attacker_events.get(cluster, []),
                          dust_total=attacker_dust.get(cluster, 0.0),
                          addresses=sorted(attacker_addresses.get(cluster, set())))
                 for cluster, victims in attacker_victims.items()]
    campaigns.sort(key=lambda campaign: (-len(campaign.victims), campaign.cluster))
    return pd.DataFrame(rows), campaigns


def _numbers(*candidates: Any) -> list[float]:
    """The first of these that yields numbers, so a one-sided row still has value."""
    for values in candidates:
        if isinstance(values, (list, tuple, np.ndarray)) and len(values):
            try:
                return [float(value) for value in values]
            except (TypeError, ValueError):
                continue
    return []


def sweeps(corr: CorrelationResult) -> pd.DataFrame:
    """The pooled-deposit-then-one-exit shape, measured over a WINDOW.

    WHY NOT A SHARE OF LIFETIME INFLOW
    ----------------------------------
    The obvious rule is "the largest outflow is most of everything this wallet ever
    received". It does not survive contact with a real capture: measured on a
    planted sweep, that ratio was 0.19, because the collection wallet also received
    ordinary payments -- which is what a real one does too, and the synthetic
    capture mixes them in on purpose so the classes are not trivially separable. A
    rule that only fires on a wallet with no other history would fire on nothing in
    production.

    The signature is concentration in TIME, so that is what is measured: how much
    arrived in the day before a large outflow, against how large that outflow was.
    An exit that is several times everything that arrived in the previous day is a
    decision; ordinary churn is not.
    """
    flows = corr.flows
    if flows.empty:
        return pd.DataFrame(columns=["entity_id"])

    work = flows.copy()
    work["value"] = pd.to_numeric(work["value"], errors="coerce").fillna(0.0)
    work["timestamp"] = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    work = work.dropna(subset=["timestamp"]).sort_values("timestamp")

    rows: list[dict[str, Any]] = []
    for entity_id, entity_flows in work.groupby("dst", sort=False):
        incoming = entity_flows[["value", "timestamp"]]
        outgoing = work[work["src"] == entity_id]
        if outgoing.empty:
            rows.append({"entity_id": entity_id, "sweep_score": 0.0,
                         "sweep_ratio": 0.0, "sweep_gap_hours": 0.0})
            continue

        exit_row = outgoing.loc[outgoing["value"].idxmax()]
        exited_at, exit_value = exit_row["timestamp"], float(exit_row["value"])
        window_start = exited_at - pd.Timedelta(hours=SWEEP_WINDOW_HOURS)
        arrived = incoming[(incoming["timestamp"] >= window_start)
                           & (incoming["timestamp"] <= exited_at)]

        arrived_value = float(arrived["value"].sum())
        ratio = exit_value / arrived_value if arrived_value > 0 else 0.0
        gap = abs((exited_at - arrived["timestamp"].max()).total_seconds()) / 3600.0             if not arrived.empty else 0.0

        qualifies = (
            len(arrived) >= SWEEP_MIN_DEPOSITS
            and ratio >= SWEEP_MIN_CONCENTRATION
            and gap <= SWEEP_MAX_GAP_HOURS
        )
        rows.append({
            "entity_id": entity_id,
            "sweep_score": 1.0 if qualifies else 0.0,
            "sweep_ratio": float(min(ratio, 999.0)),
            "sweep_gap_hours": float(gap),
        })

    table = pd.DataFrame(rows)
    # Entities that received nothing still need a row, or the merge drops them from
    # the feature matrix and they vanish from the analysis entirely.
    absent = corr.entities.loc[~corr.entities["entity_id"].isin(table["entity_id"]),
                               ["entity_id"]]
    if not absent.empty:
        table = pd.concat([table, absent.assign(sweep_score=0.0, sweep_ratio=0.0,
                                                sweep_gap_hours=0.0)], ignore_index=True)
    return table


# The feature names these two detectors contribute. Declared here so the feature
# builder and the interface's plain-language labels cannot drift from the module
# that produces them.
CAMPAIGN_FEATURES: tuple[str, ...] = (
    "lookalike_score",
    "dust_received",
    "dust_campaign_size",
    "sweep_score",
    "sweep_ratio",
)
