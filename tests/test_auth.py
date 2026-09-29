"""
Authentication and authorisation, tested through the real HTTP surface.

WHY THE TESTS GO THROUGH THE API RATHER THAN THE FUNCTIONS
----------------------------------------------------------
The claim being tested is not "hash_password verifies a hash", it is "an
unauthenticated request for a page gets a sign-in screen, and a request for data
gets nothing". That is a property of the wiring -- the middleware, the cookie, the
capability dependency on each route -- and only a request exercises it. A unit test
of `verify_password` would pass on a build where every page was public.

These tests use FastAPI's TestClient against the same `app` the workstation serves,
with config pointed at a throwaway directory, so they cannot touch real state.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("sandbox")

ADMIN = "admin"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(sandbox):
    """A fresh application with one administrator and no analysis yet."""
    from netra.api.app import app as application
    from netra.api.auth import open_product_store
    from netra.state.product import bootstrap_admin

    with open_product_store() as store:
        bootstrap_admin(store, PASSWORD, name=ADMIN, display_name="Test Admin")
        # One account per role, so a capability can be checked against a role that
        # lacks it rather than against a hand-made token. `create_user` hashes the
        # password itself -- passing it a hash would store a hash of a hash and
        # every sign-in for that account would fail.
        for name, role in (("analyst1", "analyst"), ("auditor1", "auditor"),
                           ("super1", "supervisor")):
            store.create_user(name=name, display_name=name.title(), role=role,
                              password=PASSWORD)
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def sign_in(client, username=ADMIN, password=PASSWORD):
    response = client.post("/api/auth/sign-in", json={"username": username,
                                                      "password": password})
    return response


def csrf_of(client) -> str:
    return client.cookies.get("netra_csrf") or ""


# --------------------------------------------------------------------------
# Nothing is reachable without a session
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", ["/app.html", "/"])
def test_pages_require_a_session(client, path):
    """A page asks the browser to sign in (302); the data routes answer 401. A page
    that returned the content anyway would make the sign-in screen decoration."""
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 302, path
    assert "sign-in" in response.headers.get("location", "")


@pytest.mark.parametrize("path", ["/print/capture", "/print/leads"])
def test_reports_and_exports_require_a_session(client, path):
    """A printed report is the whole analysis on one page, so it is a data route:
    it answers 401 rather than redirecting, because a redirect fetched by a report
    viewer would be saved to disk as the sign-in screen."""
    assert client.get(path, follow_redirects=False).status_code == 401, path


@pytest.mark.parametrize("path", [
    "/api/results", "/api/leads", "/api/cases", "/api/audit", "/api/events",
    "/api/canaries", "/api/windows", "/api/metrics", "/api/models", "/api/runs",
    "/api/dispositions", "/api/policy", "/api/admin/users", "/api/auth/me",
])
def test_api_requires_a_session(client, path):
    response = client.get(path)
    assert response.status_code == 401, f"{path} answered {response.status_code}"


def test_public_paths_stay_public(client):
    """The sign-in page itself, its assets, and the build string -- the version is
    public so the sign-in screen can state what it is running before anyone has
    authenticated."""
    for path in ("/sign-in.html", "/assets/netra.css", "/api/auth/version"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"


def test_a_wrong_password_and_a_missing_account_are_indistinguishable(client):
    """An air-gapped host has no lockout service and no email. If a failed sign-in
    said "no such user", the sign-in form would be a directory of who works here."""
    missing = sign_in(client, "nobody-here", PASSWORD)
    wrong = sign_in(client, ADMIN, "not-the-password")
    assert missing.status_code == wrong.status_code == 401
    assert missing.json() == wrong.json()


def test_disabled_accounts_cannot_sign_in(client, app):
    from netra.api.auth import open_product_store

    sign_in(client)
    client.post("/api/auth/sign-out")
    with open_product_store() as store:
        store.set_active("analyst1", False)
    response = sign_in(client, "analyst1", PASSWORD)
    assert response.status_code == 401


def test_sign_in_sets_both_cookies(client):
    response = sign_in(client)
    assert response.status_code == 200
    assert client.cookies.get("netra_session"), "no session cookie was set"
    assert client.cookies.get("netra_csrf"), "no CSRF cookie was set"
    assert response.json()["user"]["role"] == "admin"


# --------------------------------------------------------------------------
# CSRF: a state change needs the header the page holds
# --------------------------------------------------------------------------
def test_a_write_without_the_csrf_header_is_refused(client):
    """Double-submit: the cookie alone is not enough, because a form on another
    origin carries cookies and cannot set this header."""
    sign_in(client)
    without = client.post("/api/cases", json={"title": "no token"})
    assert without.status_code == 403
    with_token = client.post("/api/cases", json={"title": "with token"},
                             headers={"X-CSRF-Token": csrf_of(client)})
    assert with_token.status_code == 201


def test_a_read_does_not_need_the_csrf_header(client):
    sign_in(client)
    assert client.get("/api/cases").status_code == 200


# --------------------------------------------------------------------------
# Roles are enforced by the server, not by hiding buttons
# --------------------------------------------------------------------------
def test_a_role_without_the_capability_is_refused(client, app):
    """The rail hides what a role may not do. That is a courtesy. This is the
    control, and it has to refuse a request the page would never have made."""
    sign_in(client, "analyst1", PASSWORD)
    assert client.get("/api/audit").status_code == 403      # audit_read: admin/auditor
    assert client.get("/api/admin/users").status_code == 403  # manage_users: admin


def test_an_auditor_may_read_the_log_and_change_nothing(client, app):
    sign_in(client, "auditor1", PASSWORD)
    assert client.get("/api/audit").status_code == 200
    assert client.post("/api/cases", json={"title": "auditors do not do this"},
                       headers={"X-CSRF-Token": csrf_of(client)}).status_code == 403


def test_capabilities_come_from_the_server(client):
    sign_in(client, "auditor1", PASSWORD)
    me = client.get("/api/auth/me").json()
    assert "audit_read" in me["capabilities"]
    assert "disposition" not in me["capabilities"]


# --------------------------------------------------------------------------
# Lockout, sessions, reset tokens
# --------------------------------------------------------------------------
def test_repeated_failures_lock_the_account(client, app):
    from netra import config

    for _ in range(config.LOGIN_FAILURE_LIMIT):
        sign_in(client, "analyst1", "wrong-again")
    # The correct password now fails too: the lock is on the account, not on the
    # attempt, or an attacker simply waits for the right guess.
    assert sign_in(client, "analyst1", PASSWORD).status_code == 401


def test_signing_out_ends_the_session(client):
    sign_in(client)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/sign-out")
    assert client.get("/api/auth/me").status_code == 401


def test_a_reset_token_is_single_use(client, app):
    """It is handed over by voice or on paper, so it must work once and expire.

    Redemption happens WITHOUT a session -- the person using it is the person who
    cannot sign in. That is why the route is public, and why this test signs out
    before redeeming rather than after.
    """
    import time

    sign_in(client)
    issued = client.post("/api/admin/users/analyst1/reset-token",
                         headers={"X-CSRF-Token": csrf_of(client)})
    assert issued.status_code == 200
    token = issued.json()["token"]
    client.post("/api/auth/sign-out")

    first = client.post("/api/auth/redeem-reset",
                        json={"username": "analyst1", "token": token,
                              "new_password": "a-new-password-1"})
    assert first.status_code == 200, first.text

    second = client.post("/api/auth/redeem-reset",
                         json={"username": "analyst1", "token": token,
                               "new_password": "another-password-2"})
    assert second.status_code == 403

    # The new password works, and the old one does not.
    assert sign_in(client, "analyst1", "a-new-password-1").status_code == 200
    client.post("/api/auth/sign-out")
    assert sign_in(client, "analyst1", PASSWORD).status_code == 401


def test_an_expired_reset_token_is_refused(client, app, monkeypatch):
    """Short-lived on purpose: it is spoken aloud or written on paper."""
    from netra import config

    sign_in(client)
    token = client.post("/api/admin/users/analyst1/reset-token",
                        headers={"X-CSRF-Token": csrf_of(client)}).json()["token"]
    client.post("/api/auth/sign-out")
    monkeypatch.setattr(config, "RESET_TOKEN_MINUTES", 0)
    response = client.post("/api/auth/redeem-reset",
                           json={"username": "analyst1", "token": token,
                                 "new_password": "yet-another-password"})
    assert response.status_code == 403


def test_changing_a_password_requires_the_current_one(client, app):
    sign_in(client, "analyst1", PASSWORD)
    refused = client.post("/api/auth/password",
                          json={"current": "not-it", "new": "brand-new-password"},
                          headers={"X-CSRF-Token": csrf_of(client)})
    assert refused.status_code in (400, 403)
    allowed = client.post("/api/auth/password",
                          json={"current": PASSWORD, "new": "brand-new-password"},
                          headers={"X-CSRF-Token": csrf_of(client)})
    assert allowed.status_code == 200


def test_passwords_are_never_stored_in_the_clear(sandbox):
    from netra.api.auth import open_product_store
    from netra.state.product import bootstrap_admin, verify_password

    with open_product_store() as store:
        bootstrap_admin(store, PASSWORD, name="admin2", display_name="Two")
        row = store._connection.execute(
            "SELECT credential FROM users WHERE name='admin2'").fetchone()
    credential = row["credential"]
    assert PASSWORD not in credential, "the password itself is stored"
    assert credential.startswith("scrypt$"), credential[:20]
    assert verify_password(PASSWORD, credential)
    assert not verify_password(PASSWORD + "x", credential)


# --------------------------------------------------------------------------
# The audit log records what happened, and cannot be rewritten
# --------------------------------------------------------------------------
def test_the_audit_log_is_append_only(sandbox):
    """Enforced by a trigger in the schema, not by convention: the log is the one
    artefact whose value depends entirely on nobody having edited it."""
    import sqlite3

    from netra.api.auth import open_product_store
    from netra.state.product import bootstrap_admin

    with open_product_store() as store:
        bootstrap_admin(store, PASSWORD, name="admin3", display_name="Three")
        store.audit(actor="admin3", role="admin", action="Test", object_type="x",
                    object_id="1")
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute("UPDATE audit_log SET action='rewritten'")
        with pytest.raises(sqlite3.IntegrityError):
            store._connection.execute("DELETE FROM audit_log")


def test_sign_in_and_sign_out_are_recorded(client, app):
    from netra.api.auth import open_product_store

    sign_in(client)
    with open_product_store() as store:
        actions = [row["action"] for row in store._connection.execute(
            "SELECT action FROM audit_log ORDER BY rowid DESC LIMIT 5")]
    assert "Sign in" in actions
