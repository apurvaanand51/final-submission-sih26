"""
The state layer: identity that does not move, history that is not rewritten, and
the organisation's records, which are not the analysis's to delete.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import pytest

from netra import config
from netra.operations.pipeline import replay
from netra.state.product import ProductStore, bootstrap_admin
from netra.state.store import MonitoringStore

pytestmark = pytest.mark.usefixtures("sandbox")


def a_capture(path, entities: int = 12, rows_each: int = 3):
    """A tiny capture with `entities` independent co-spending clusters."""
    records = []
    for entity in range(entities):
        inputs = [f"bc1q{entity:02d}{index}" for index in range(2)]
        for row in range(rows_each):
            records.append({
                "timestamp": f"2026-08-1{row + 1}T{entity % 24:02d}:00:00+00:00",
                "txid": f"{entity:02d}{row:03d}",
                "input_addresses": "|".join(inputs),
                "output_addresses": f"bc1qout{entity:02d}{row}",
                "input_amounts": "|".join(["1.5"] * len(inputs)),
                "output_amounts": "3.0",
                "src_ip": f"10.0.{entity}.1",
                "dst_ip": f"10.0.{entity}.2",
                "geo_country": "GB" if entity % 2 else "DE",
                "asn": "AS2856",
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_csv(path, index=False)
    return path


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
def test_an_id_is_assigned_once_and_never_changes(sandbox):
    """`NTR-####` is derived from the addresses, so the same wallet group keeps
    the same name across batches. An id that moves makes every note, case and
    report about it stale without saying so."""
    from netra.identify.cluster import stable_entity_id

    first = stable_entity_id("bc1qanchor0001", taken=set())
    assert first.startswith("NTR-") and len(first.split("-")[1]) == 4
    taken = {first}
    second = stable_entity_id("bc1qanchor0002", taken=taken)
    assert second not in taken


def test_a_collision_extends_the_id_rather_than_reusing_it(sandbox):
    """Two different groups must never share an id. When a new entity hashes onto
    one already in use, the NEW entity gets more digits: the assigned id is the
    one that may not change."""
    from netra.identify.cluster import stable_entity_id

    # The caller owns the set of ids in use, and adds each id it assigns: the
    # escalation is applied to the NEW entity, so the function cannot also be the
    # one that remembers what has been handed out.
    taken: set[str] = set()
    ids = []
    for index in range(4000):
        assigned = stable_entity_id(f"bc1qanchor{index:04d}", taken)
        taken.add(assigned)
        ids.append(assigned)
    assert len(set(ids)) == len(ids), "two entities were given the same id"
    assert all(identifier.startswith("NTR-") for identifier in ids)
    lengths = {len(identifier.split("-")[1]) for identifier in ids}
    assert 4 in lengths
    assert lengths <= {4, 6, 8, 10}


def test_the_same_capture_produces_the_same_ids(sandbox):
    """Content-derived identity means a re-run must reproduce it exactly, or two
    runs of the same file cannot be compared."""
    path = a_capture(sandbox / "data" / "transactions.csv")
    first = replay(store_path=sandbox / "state" / "one.sqlite", dataset=path,
                   data_dir=sandbox / "data", reset=True)
    with MonitoringStore(sandbox / "state" / "one.sqlite") as store:
        # address -> entity, so de-duplicate: a group holds several addresses.
        ids_first = sorted(set(store.pinned_keys().values()))

    second = replay(store_path=sandbox / "state" / "two.sqlite", dataset=path,
                    data_dir=sandbox / "data", reset=True)
    with MonitoringStore(sandbox / "state" / "two.sqlite") as store:
        ids_second = sorted(set(store.pinned_keys().values()))

    assert ids_first == ids_second
    assert first["windows"] and second["windows"]


# --------------------------------------------------------------------------
# A reset clears the analysis, and only the analysis
# --------------------------------------------------------------------------
def test_a_replay_does_not_delete_the_organisations_records(sandbox):
    """Accounts, the audit log and cases live in the same file as the analysis.
    The replay used to delete the FILE to start from an empty history, which took
    the audit log with it -- the one artefact whose whole value is that nobody has
    edited it."""
    database = config.STATE_DB
    first = a_capture(sandbox / "data" / "one.csv")
    second = a_capture(sandbox / "data" / "two.csv", entities=20)

    with ProductStore(database) as store:
        bootstrap_admin(store, "a-long-enough-password", name="admin",
                        display_name="Admin")
        store.audit(actor="admin", role="admin", action="Open case",
                    object_type="case", object_id="C-001")
        store.create_case("a case that must survive", owner="admin")
        before = [row["action"] for row in store.audit_entries(limit=50)]

    replay(store_path=database, dataset=first, data_dir=sandbox / "data", reset=True)
    replay(store_path=database, dataset=second, data_dir=sandbox / "data", reset=True)

    with ProductStore(database) as store:
        assert store.user("admin") is not None, "the account was deleted by a replay"
        assert [case["id"] for case in store.cases()] == ["C-001"]
        after = [row["action"] for row in store.audit_entries(limit=50)]
    assert set(before) <= set(after), "the audit log lost entries"


def test_a_reset_replaces_the_analysis_rather_than_appending(sandbox):
    """Two captures whose dates do not overlap must not be reported as one traffic
    dump. The union view would otherwise sum them."""
    database = config.STATE_DB
    first = a_capture(sandbox / "data" / "one.csv", entities=10)
    second = a_capture(sandbox / "data" / "two.csv", entities=25)

    replay(store_path=database, dataset=first, data_dir=sandbox / "data", reset=True)
    with MonitoringStore(database) as store:
        after_first = len(store.windows())

    replay(store_path=database, dataset=second, data_dir=sandbox / "data", reset=True)
    with MonitoringStore(database) as store:
        after_second = len(store.windows())
        entities = store.summary().get("entities",
                                       len(store.pinned_keys()))

    assert after_second == after_first, "the second capture was appended, not replayed"
    assert entities >= 25, "the second capture's entities are not the ones in the store"


def test_appending_is_still_possible_on_purpose(sandbox):
    """The incremental case is real -- a new batch for a capture already loaded --
    and it is a parameter rather than the default because getting it wrong is
    silent."""
    database = config.STATE_DB
    first = a_capture(sandbox / "data" / "one.csv", entities=8)
    second = a_capture(sandbox / "data" / "two.csv", entities=8)

    replay(store_path=database, dataset=first, data_dir=sandbox / "data", reset=True)
    with MonitoringStore(database) as store:
        before = len(store.windows())

    replay(store_path=database, dataset=second, data_dir=sandbox / "data",
           reset=False)
    with MonitoringStore(database) as store:
        after = len(store.windows())

    assert after >= before, "an explicit append removed history"


# --------------------------------------------------------------------------
# The audit log
# --------------------------------------------------------------------------
def test_the_audit_log_cannot_be_edited_or_deleted(sandbox):
    """Enforced by triggers in the schema rather than by convention: this is the
    artefact whose value depends on nobody having edited it."""
    with ProductStore(config.STATE_DB) as store:
        store.audit(actor="analyst", role="analyst", action="Sign in")
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute("UPDATE audit_log SET action = 'something else'")
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute("DELETE FROM audit_log")
        store._connection.rollback()
        assert store.audit_entries(limit=5)


def test_every_action_is_recorded_with_who_and_what(sandbox):
    with ProductStore(config.STATE_DB) as store:
        store.audit(actor="supervisor", role="supervisor", action="Disposition",
                    object_type="entity", object_id="NTR-0001", detail="confirmed")
        entry = store.audit_entries(limit=1)[0]
    assert entry["actor"] == "supervisor"
    assert entry["role"] == "supervisor"
    assert entry["object_id"] == "NTR-0001"
    assert entry["at"]


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------
def test_a_session_is_resolved_with_its_role(sandbox):
    """The session row carries no role: reading it from the session alone raised
    an IndexError, and inventing one would be worse than reading it."""
    from netra.state.product import hash_password

    with ProductStore(config.STATE_DB) as store:
        user = store.create_user("analyst1", "Analyst One", "analyst",
                                 hash_password("a-long-enough-password"))
        session = store.open_session(user)
        resolved, reason = store.session(session.id)
    assert resolved is not None, reason
    assert resolved.role == "analyst"
    assert resolved.user == "analyst1"
    assert resolved.csrf


def test_a_closed_session_stops_resolving(sandbox):
    from netra.state.product import hash_password

    with ProductStore(config.STATE_DB) as store:
        user = store.create_user("analyst2", "Analyst Two", "analyst",
                                 hash_password("a-long-enough-password"))
        session = store.open_session(user)
        store.close_session(session.id)
        resolved, reason = store.session(session.id)
    assert resolved is None
    assert "closed" in reason or "session" in reason


def test_the_review_floor_is_recorded_with_who_changed_it(sandbox):
    """Changing what counts as a lead changes what the team looks at, so the
    change is a record, not a setting."""
    with ProductStore(config.STATE_DB) as store:
        store.set_threshold(65, actor="supervisor")
        assert store.threshold() == 65
        history = store.threshold_history()
    assert history and history[0]["value"] == 65
    assert history[0]["set_by"] == "supervisor"
