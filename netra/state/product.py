"""
The product's own state: who may use this, what they did, and what is being worked.

WHY THIS IS SEPARATE FROM `state/store.py`
------------------------------------------
`store.py` holds what the ANALYSIS produces — scores, events, alerts, identity.
This holds what the ORGANISATION produces — users, sessions, the audit trail,
cases, dispositions, canaries, model versions, thresholds.

They are deliberately different concerns with different lifetimes. Scores are
derived and can be recomputed from the capture; an audit entry cannot be
recomputed from anything and must never be lost. Keeping them in separate modules
makes that difference visible, and they happen to share one SQLite file because on
an air-gapped host one file is one thing to back up.

TWO RULES THAT ARE ENFORCED HERE RATHER THAN DOCUMENTED
-------------------------------------------------------
  * **The audit log is append-only.** There is no update and no delete method, and
    the table has a trigger that refuses both. A log that can be edited is not
    evidence.
  * **Credentials are never stored, only verified.** scrypt with a per-user salt and
    the parameters recorded per credential, so they can be strengthened later
    without invalidating anyone's password. Chosen from the standard library on
    purpose: a new wheel is a new way for an air-gapped install to fail.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from netra import config

# --------------------------------------------------------------------------
# Roles. Named here because the API, the UI and the audit entries all refer to
# them, and a role that exists in one of those and not the others is a permission
# bug waiting to happen.
# --------------------------------------------------------------------------
ROLES: tuple[str, ...] = ("analyst", "supervisor", "admin", "auditor")

# What each role may do. The API checks these; the interface only hides what they
# forbid, because hiding a button is never access control.
PERMISSIONS: dict[str, set[str]] = {
    "analyst": {"view", "disposition", "case_edit", "export", "canary_label"},
    "supervisor": {"view", "disposition", "case_edit", "export", "canary_label",
                   "threshold", "assign"},
    # An administrator runs the deployment, and in a six-person unit that includes
    # doing the work: an admin who cannot open a case or read the audit log is an
    # account that exists only on paper. The auditor role is the read-only one.
    "admin": {"view", "disposition", "case_edit", "export", "canary_label",
              "threshold", "assign", "audit_read",
              "manage_users", "manage_models", "backup", "configure"},
    "auditor": {"view", "audit_read"},
}

# scrypt parameters. Stored per credential so they can be raised later; the value
# recorded at creation is the value that credential is verified with, forever.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
SESSION_COOKIE = "netra_session"
CSRF_COOKIE = "netra_csrf"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Password hashing and tokens.
# --------------------------------------------------------------------------
def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """`scrypt$n$r$p$salt$hash` — one string carrying everything verification needs."""
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "$".join(("scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
                     salt.hex(), digest.hex()))


def verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison against the stored credential."""
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
                                  n=int(n), r=int(r), p=int(p), dklen=len(digest_hex) // 2)
        return hmac.compare_digest(expected.hex(), digest_hex)
    except (ValueError, TypeError):
        # A malformed stored credential must fail closed, not raise into a login
        # handler that might then treat the user as authenticated.
        return False


def new_token(bytes_: int = 32) -> str:
    return secrets.token_urlsafe(bytes_)


@dataclass
class Session:
    id: str
    user: str
    role: str
    created_at: str
    last_seen: str
    csrf: str

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "user": self.user, "role": self.role,
                "created_at": self.created_at, "last_seen": self.last_seen}


@dataclass
class User:
    name: str
    role: str
    display_name: str
    active: bool
    created_at: str
    failed_logins: int = 0
    locked_until: str | None = None

    @property
    def initials(self) -> str:
        """For the avatar in the corner of every screen."""
        parts = [part for part in self.display_name.replace(".", " ").split() if part]
        return "".join(part[0].upper() for part in parts[:2]) or self.name[:1].upper()

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "role": self.role, "display_name": self.display_name,
                "active": self.active, "created_at": self.created_at,
                "initials": self.initials, "locked": bool(self.locked_until
                                                          and self.locked_until > _now())}


class ProductStore:
    """Every table the organisation owns, and nothing the analysis owns."""

    def __init__(self, path: Path | str = config.STATE_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ProductStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ schema
    def _create_schema(self) -> None:
        self._connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            name            TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL,
            role            TEXT NOT NULL,
            credential      TEXT NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            failed_logins   INTEGER NOT NULL DEFAULT 0,
            locked_until    TEXT
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id              TEXT PRIMARY KEY,
            user            TEXT NOT NULL REFERENCES users(name),
            csrf            TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            last_seen       TEXT NOT NULL
        );

        -- Append-only by construction: a trigger refuses UPDATE and DELETE, so the
        -- guarantee does not depend on every future caller remembering.
        CREATE TABLE IF NOT EXISTS audit_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            at              TEXT NOT NULL,
            actor           TEXT NOT NULL,
            role            TEXT,
            action          TEXT NOT NULL,
            object_type     TEXT,
            object_id       TEXT,
            evidence_hash   TEXT,
            engine_version  TEXT,
            model_version   TEXT,
            detail          TEXT,
            result          TEXT
        );
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
            BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
            BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'the audit log is append-only'); END;

        CREATE TABLE IF NOT EXISTS cases (
            id              TEXT PRIMARY KEY,
            title           TEXT NOT NULL,
            state           TEXT NOT NULL DEFAULT 'open',
            owner           TEXT,
            severity        TEXT,
            opened_at       TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            closed_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS case_items (
            case_id         TEXT NOT NULL REFERENCES cases(id),
            kind            TEXT NOT NULL,        -- lead | wallet | canary | event
            ref             TEXT NOT NULL,
            label           TEXT,
            score_at_link   INTEGER,
            band_at_link    TEXT,
            linked_at       TEXT NOT NULL,
            linked_by       TEXT,
            PRIMARY KEY (case_id, kind, ref)
        );

        CREATE TABLE IF NOT EXISTS case_notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id         TEXT NOT NULL REFERENCES cases(id),
            author          TEXT,
            at              TEXT NOT NULL,
            text            TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dispositions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_key      TEXT NOT NULL,
            window_id       INTEGER,
            label           TEXT NOT NULL,        -- closed_false_positive | closed_escalated
            source          TEXT NOT NULL,        -- disposition | confirmed | imported
            strength        REAL NOT NULL DEFAULT 0.5,
            actor           TEXT,
            at              TEXT NOT NULL,
            note            TEXT
        );

        CREATE TABLE IF NOT EXISTS canaries (
            address         TEXT PRIMARY KEY,
            owner           TEXT,
            purpose         TEXT,
            planted_at      TEXT NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS canary_observations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            address         TEXT NOT NULL REFERENCES canaries(address),
            counterparty    TEXT,
            cluster         TEXT,
            amount          REAL,
            direction       TEXT,                 -- in | out
            at              TEXT,
            seen_at         TEXT NOT NULL,
            label           TEXT,
            label_by        TEXT,
            label_at        TEXT
        );

        CREATE TABLE IF NOT EXISTS model_registry (
            version         TEXT PRIMARY KEY,
            algorithm       TEXT NOT NULL,
            artifact_hash   TEXT,
            trained_on      TEXT,
            n_labels        INTEGER,
            metrics         TEXT,
            state           TEXT NOT NULL DEFAULT 'challenger',
            promoted_at     TEXT,
            promoted_by     TEXT,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id              TEXT PRIMARY KEY,
            kind            TEXT NOT NULL,
            input_name      TEXT,
            input_hash      TEXT,
            input_bytes     INTEGER,
            rows_read       INTEGER,
            rows_usable     INTEGER,
            engine_version  TEXT,
            model_version   TEXT,
            started_at      TEXT,
            finished_at     TEXT,
            output_fingerprint TEXT,
            actor           TEXT
        );

        CREATE TABLE IF NOT EXISTS thresholds (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            value           INTEGER NOT NULL,
            set_by          TEXT,
            set_at          TEXT NOT NULL,
            reason          TEXT
        );
        """)
        self._connection.commit()

    # ------------------------------------------------------------------- users
    def create_user(self, name: str, display_name: str, role: str, password: str) -> User:
        if role not in ROLES:
            raise ValueError(f"unknown role '{role}' -- expected one of {list(ROLES)}")
        self._connection.execute(
            "INSERT INTO users (name, display_name, role, credential, active, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (name, display_name, role, hash_password(password), _now()))
        self._connection.commit()
        return self.user(name)                       # type: ignore[return-value]

    def user(self, name: str) -> User | None:
        row = self._connection.execute(
            "SELECT * FROM users WHERE name = ?", (name,)).fetchone()
        return self._to_user(row) if row else None

    def users(self) -> list[User]:
        rows = self._connection.execute(
            "SELECT * FROM users ORDER BY role, name").fetchall()
        return [self._to_user(row) for row in rows]

    @staticmethod
    def _to_user(row: sqlite3.Row) -> User:
        return User(name=row["name"], role=row["role"], display_name=row["display_name"],
                    active=bool(row["active"]), created_at=row["created_at"],
                    failed_logins=row["failed_logins"], locked_until=row["locked_until"])

    def set_active(self, name: str, active: bool) -> None:
        self._connection.execute("UPDATE users SET active = ? WHERE name = ?",
                                 (1 if active else 0, name))
        self._connection.commit()

    def set_password(self, name: str, password: str) -> None:
        """Also clears any lockout: an administrator resetting a password is
        resolving the situation the lockout was protecting against."""
        self._connection.execute(
            "UPDATE users SET credential = ?, failed_logins = 0, locked_until = NULL "
            "WHERE name = ?", (hash_password(password), name))
        self._connection.commit()

    def verify(self, name: str, password: str) -> tuple[User | None, str]:
        """Returns `(user, reason)`. The reason is for the audit log, never for the
        sign-in screen -- telling a caller whether an account exists is a gift to
        anyone probing it."""
        user = self.user(name)
        if user is None:
            # Hash anyway, so a missing account and a wrong password take
            # comparable time. Returning immediately makes the difference
            # measurable.
            hash_password(password)
            return None, "no such account"
        if not user.active:
            return None, "account disabled"
        if user.locked_until and user.locked_until > _now():
            return None, f"locked until {user.locked_until}"
        row = self._connection.execute(
            "SELECT credential FROM users WHERE name = ?", (name,)).fetchone()
        if not verify_password(password, row["credential"]):
            self._register_failure(name)
            return None, "wrong password"
        self._connection.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE name = ?",
            (name,))
        self._connection.commit()
        return user, "ok"

    def _register_failure(self, name: str) -> None:
        row = self._connection.execute(
            "SELECT failed_logins FROM users WHERE name = ?", (name,)).fetchone()
        failures = int(row["failed_logins"]) + 1 if row else 1
        locked = None
        if failures >= config.LOGIN_FAILURE_LIMIT:
            locked = (datetime.now(timezone.utc)
                      + timedelta(minutes=config.LOGIN_LOCK_MINUTES)).strftime(
                          "%Y-%m-%dT%H:%M:%SZ")
        self._connection.execute(
            "UPDATE users SET failed_logins = ?, locked_until = ? WHERE name = ?",
            (failures, locked, name))
        self._connection.commit()

    def unlock(self, name: str) -> None:
        self._connection.execute(
            "UPDATE users SET failed_logins = 0, locked_until = NULL WHERE name = ?",
            (name,))
        self._connection.commit()

    # ---------------------------------------------------------------- sessions
    def open_session(self, user: User) -> Session:
        session = Session(id=new_token(24), user=user.name, role=user.role,
                          created_at=_now(), last_seen=_now(), csrf=new_token(24))
        self._connection.execute(
            "INSERT INTO sessions (id, user, csrf, created_at, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (session.id, session.user, session.csrf, session.created_at, session.last_seen))
        self._connection.commit()
        return session

    def session(self, session_id: str | None) -> tuple[Session | None, str]:
        """Resolve a session id, enforcing both lifetimes.

        Two limits because they defend against different things: the idle timeout
        against an unattended terminal, the absolute lifetime against a session
        that has simply existed for too long to keep trusting.
        """
        if not session_id:
            return None, "no session"
        # The role is read from the USER, not stored on the session. A session that
        # carried its own copy would keep granting yesterday's permissions after an
        # administrator changed somebody's role -- and a revoked role is exactly the
        # change that must take effect at once.
        row = self._connection.execute(
            "SELECT s.*, u.role AS role FROM sessions s "
            "JOIN users u ON u.name = s.user WHERE s.id = ?", (session_id,)).fetchone()
        if row is None:
            return None, "unknown session"
        created = datetime.strptime(row["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        seen = datetime.strptime(row["last_seen"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if now - seen > timedelta(minutes=config.SESSION_IDLE_MINUTES):
            self.close_session(session_id)
            return None, "signed out after idle timeout"
        if now - created > timedelta(hours=config.SESSION_ABSOLUTE_HOURS):
            self.close_session(session_id)
            return None, "session expired"
        self._connection.execute("UPDATE sessions SET last_seen = ? WHERE id = ?",
                                 (_now(), session_id))
        self._connection.commit()
        return Session(id=row["id"], user=row["user"], role=row["role"],
                       created_at=row["created_at"], last_seen=row["last_seen"],
                       csrf=row["csrf"]), "ok"

    def close_session(self, session_id: str) -> None:
        self._connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self._connection.commit()

    def live_sessions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT s.id, s.user, u.display_name, u.role, s.created_at, s.last_seen "
            "FROM sessions s JOIN users u ON u.name = s.user "
            "ORDER BY s.last_seen DESC").fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------- audit
    def audit(self, *, actor: str, action: str, role: str | None = None,
              object_type: str | None = None, object_id: str | None = None,
              evidence_hash: str | None = None, model_version: str | None = None,
              detail: str | None = None, result: str = "OK") -> None:
        """Append one entry. The only write path to the log, by design."""
        self._connection.execute(
            "INSERT INTO audit_log (at, actor, role, action, object_type, object_id, "
            "evidence_hash, engine_version, model_version, detail, result) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_now(), actor, role, action, object_type, object_id, evidence_hash,
             config.ENGINE_VERSION, model_version, detail, result))
        self._connection.commit()

    def audit_entries(self, *, actor: str | None = None, action: str | None = None,
                      limit: int = 200) -> list[dict[str, Any]]:
        # `actor="*"` means every actor. It was read literally as "the actor whose
        # name is an asterisk", so the reset-token lookup in netra/api/auth.py found
        # nothing and EVERY password reset was refused -- a defect only an
        # end-to-end reset test can see, because the refusal message is the same one
        # a genuinely bad token produces.
        query = "SELECT * FROM audit_log"
        clauses, params = [], []
        if actor and actor != "*":
            clauses.append("actor = ?"); params.append(actor)
        if action:
            clauses.append("action LIKE ?"); params.append(f"%{action}%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self._connection.execute(query, params).fetchall()]

    # ------------------------------------------------------------------- cases
    def create_case(self, title: str, owner: str | None, severity: str | None = None) -> str:
        """Case ids are sequential and human: C-001, C-002. A case is referred to out
        loud and written in a notebook, so it needs an identifier someone can say."""
        row = self._connection.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
        case_id = f"C-{int(row['n']) + 1:03d}"
        stamp = _now()
        self._connection.execute(
            "INSERT INTO cases (id, title, state, owner, severity, opened_at, updated_at) "
            "VALUES (?, ?, 'open', ?, ?, ?, ?)",
            (case_id, title, owner, severity, stamp, stamp))
        self._connection.commit()
        return case_id

    def cases(self, state: str | None = None) -> list[dict[str, Any]]:
        query = ("SELECT c.*, "
                 "(SELECT COUNT(*) FROM case_items i WHERE i.case_id = c.id) AS items, "
                 "(SELECT COUNT(*) FROM case_items i WHERE i.case_id = c.id "
                 " AND i.band_at_link = 'critical') AS critical_items "
                 "FROM cases c")
        params: list[Any] = []
        if state:
            query += " WHERE c.state = ?"
            params.append(state)
        query += " ORDER BY c.updated_at DESC"
        rows = self._connection.execute(query, params).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            entry["age_days"] = max(0, (datetime.now(timezone.utc)
                                        - datetime.strptime(entry["opened_at"],
                                                            "%Y-%m-%dT%H:%M:%SZ")
                                        .replace(tzinfo=timezone.utc)).days)
            out.append(entry)
        return out

    def case(self, case_id: str) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM cases WHERE id = ?",
                                       (case_id,)).fetchone()
        if row is None:
            return None
        entry = dict(row)
        entry["items"] = [dict(item) for item in self._connection.execute(
            "SELECT * FROM case_items WHERE case_id = ? ORDER BY linked_at DESC",
            (case_id,)).fetchall()]
        entry["notes"] = [dict(note) for note in self._connection.execute(
            "SELECT * FROM case_notes WHERE case_id = ? ORDER BY id DESC",
            (case_id,)).fetchall()]
        return entry

    def add_case_item(self, case_id: str, kind: str, ref: str, *,
                      label: str | None = None, score: int | None = None,
                      band: str | None = None, actor: str | None = None) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO case_items (case_id, kind, ref, label, score_at_link, "
            "band_at_link, linked_at, linked_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, kind, ref, label, score, band, _now(), actor))
        self._touch_case(case_id)
        self._connection.commit()

    def add_case_note(self, case_id: str, text: str, actor: str | None) -> None:
        self._connection.execute(
            "INSERT INTO case_notes (case_id, author, at, text) VALUES (?, ?, ?, ?)",
            (case_id, actor, _now(), text))
        self._touch_case(case_id)
        self._connection.commit()

    def set_case_state(self, case_id: str, state: str, actor: str | None = None) -> None:
        closed = _now() if state == "closed" else None
        self._connection.execute(
            "UPDATE cases SET state = ?, closed_at = COALESCE(?, closed_at), "
            "updated_at = ? WHERE id = ?", (state, closed, _now(), case_id))
        self._connection.commit()

    def _touch_case(self, case_id: str) -> None:
        self._connection.execute("UPDATE cases SET updated_at = ? WHERE id = ?",
                                 (_now(), case_id))

    # ------------------------------------------------------------ dispositions
    def add_disposition(self, entity_key: str, label: str, *, window_id: int | None = None,
                        source: str = "disposition", actor: str | None = None,
                        note: str | None = None) -> None:
        """Record what an analyst concluded about one group.

        `strength` separates the two kinds of label the model can learn from: a
        disposition is a judgement made in minutes from what was on screen, while a
        confirmed outcome (a seizure, a fine, an exchange's own record) is evidence.
        Keeping them apart is what stops the weaker one from being mistaken for the
        stronger.
        """
        strength = {"confirmed": 0.95, "imported": 0.7}.get(source, 0.5)
        self._connection.execute(
            "INSERT INTO dispositions (entity_key, window_id, label, source, strength, "
            "actor, at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (entity_key, window_id, label, source, strength, actor, _now(), note))
        self._connection.commit()

    def dispositions(self, limit: int = 500) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM dispositions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    def disposition_counts(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT source, COUNT(*) AS n FROM dispositions GROUP BY source").fetchall()
        return {row["source"]: int(row["n"]) for row in rows}

    # ---------------------------------------------------------------- canaries
    def add_canary(self, address: str, *, owner: str | None = None,
                   purpose: str | None = None) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO canaries (address, owner, purpose, planted_at, active) "
            "VALUES (?, ?, ?, ?, 1)", (address, owner, purpose, _now()))
        self._connection.commit()

    def canaries(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM canary_observations o "
            " WHERE o.address = c.address) AS observations, "
            "(SELECT COUNT(*) FROM canary_observations o WHERE o.address = c.address "
            " AND o.label IS NULL) AS unlabelled FROM canaries c "
            "ORDER BY c.planted_at DESC").fetchall()]

    def observe_canary(self, address: str, *, counterparty: str | None, cluster: str | None,
                       amount: float, direction: str, at: str | None) -> None:
        self._connection.execute(
            "INSERT INTO canary_observations (address, counterparty, cluster, amount, "
            "direction, at, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (address, counterparty, cluster, amount, direction, at, _now()))
        self._connection.commit()

    def canary_observations(self, *, unlabelled_only: bool = False,
                            limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM canary_observations"
        if unlabelled_only:
            query += " WHERE label IS NULL"
        query += " ORDER BY id DESC LIMIT ?"
        return [dict(row) for row in self._connection.execute(query, (limit,)).fetchall()]

    def label_canary_observation(self, observation_id: int, label: str,
                                 actor: str) -> None:
        self._connection.execute(
            "UPDATE canary_observations SET label = ?, label_by = ?, label_at = ? "
            "WHERE id = ?", (label, actor, _now(), observation_id))
        self._connection.commit()

    # ------------------------------------------------------------------ models
    def register_model(self, version: str, *, algorithm: str, artifact_hash: str,
                       trained_on: str, n_labels: int, metrics: dict[str, Any],
                       state: str = "challenger") -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO model_registry (version, algorithm, artifact_hash, "
            "trained_on, n_labels, metrics, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (version, algorithm, artifact_hash, trained_on, n_labels,
             json.dumps(metrics), state, _now()))
        self._connection.commit()

    def models(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM model_registry ORDER BY created_at DESC").fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            entry["metrics"] = json.loads(entry["metrics"] or "{}")
            out.append(entry)
        return out

    def champion(self) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM model_registry WHERE state = 'champion' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        entry = dict(row)
        entry["metrics"] = json.loads(entry["metrics"] or "{}")
        return entry

    def promote_model(self, version: str, actor: str) -> None:
        """One champion at a time, and the swap is auditable: a rollback is just a
        promotion of the previous version, so the same path serves both."""
        self._connection.execute(
            "UPDATE model_registry SET state = 'superseded' WHERE state = 'champion'")
        self._connection.execute(
            "UPDATE model_registry SET state = 'champion', promoted_at = ?, promoted_by = ? "
            "WHERE version = ?", (_now(), actor, version))
        self._connection.commit()

    # -------------------------------------------------------------------- runs
    def record_run(self, run_id: str, *, kind: str, input_name: str, input_hash: str,
                   input_bytes: int, rows_read: int, rows_usable: int,
                   model_version: str | None, started_at: str, finished_at: str,
                   output_fingerprint: str, actor: str | None) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO runs (id, kind, input_name, input_hash, input_bytes, "
            "rows_read, rows_usable, engine_version, model_version, started_at, "
            "finished_at, output_fingerprint, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?)",
            (run_id, kind, input_name, input_hash, input_bytes, rows_read, rows_usable,
             config.ENGINE_VERSION, model_version, started_at, finished_at,
             output_fingerprint, actor))
        self._connection.commit()

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)).fetchall()]

    def run(self, run_id: str) -> dict[str, Any] | None:
        row = self._connection.execute("SELECT * FROM runs WHERE id = ?",
                                       (run_id,)).fetchone()
        return dict(row) if row else None

    # --------------------------------------------------------------- thresholds
    def set_threshold(self, value: int, actor: str | None, reason: str | None = None) -> None:
        self._connection.execute(
            "INSERT INTO thresholds (value, set_by, set_at, reason) VALUES (?, ?, ?, ?)",
            (int(value), actor, _now(), reason))
        self._connection.commit()

    def threshold(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM thresholds ORDER BY id DESC LIMIT 1").fetchone()
        return int(row["value"]) if row else config.DEFAULT_REVIEW_FLOOR

    def threshold_history(self, limit: int = 20) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(
            "SELECT * FROM thresholds ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

    # ------------------------------------------------------------------ helpers
    def fingerprint(self, blob: bytes) -> str:
        """Short content hash, for evidence rows and output provenance."""
        return hashlib.sha256(blob).hexdigest()[:16]


def bootstrap_admin(store: ProductStore, password: str, *,
                    name: str = "admin", display_name: str = "Administrator") -> User:
    """Create the first administrator.

    Exists as a function because an air-gapped host has no email and no directory
    service: somebody has to be able to create the first account locally, and the
    only safe way is an explicit one-time action rather than a default credential
    that ships in the repository.
    """
    if store.user(name):
        raise ValueError(f"user '{name}' already exists")
    user = store.create_user(name=name, display_name=display_name, role="admin",
                             password=password)
    store.audit(actor=name, role="admin", action="Bootstrap",
                object_type="user", object_id=name,
                detail="first administrator created locally")
    return user
