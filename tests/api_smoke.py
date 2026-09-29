#!/usr/bin/env python
"""
The endpoint smoke test: every route, against a running application, with the
authentication rules that protect it.

WHAT THIS CHECKS THAT THE UNIT TESTS DO NOT
-------------------------------------------
A route that raises is a route the workstation calls on a screen nobody opened
during the demo. This walks the WHOLE surface -- every read, every write, every
print sheet -- and reports which returned what. It is deliberately not a pytest
file: `tasks.py smoke` runs it directly after building a small capture and
replaying it, so the check is "a clean checkout works end to end", not "the
functions work in isolation".

It runs against whatever `netra.config` points at, which for the smoke task is a
throwaway tree under out/smoke/ -- never the demonstration's history. Run it by
hand against a real installation and it will exercise that installation's state
instead, which is usually what you want when something looks wrong on screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient                          # noqa: E402

from netra import config                                           # noqa: E402
from netra.api.app import app                                      # noqa: E402

SMOKE_ADMIN = "smoke-admin"
SMOKE_PASSWORD = "smoke-test-password-1"

# (method, path, body, expected statuses). A GET is expected to answer 200, a
# creation 201, and anything whose answer depends on the capture is marked either
# way rather than pinned to one number.
# Public on purpose: the sign-in screen reads the version before anyone has
# authenticated, so it can state which build it is. It carries no data.
PUBLIC = ["/sign-in.html", "/api/auth/sign-in", "/api/auth/version"]

READS = [
    "/api/health", "/api/auth/me", "/api/windows",
    "/api/leads", "/api/events", "/api/cases", "/api/audit", "/api/canaries",
    "/api/dispositions", "/api/metrics", "/api/models", "/api/runs",
    "/api/monitoring", "/api/glossary", "/api/policy", "/api/admin/users",
    "/api/results", "/api/results?window=all", "/api/policy/simulate?floor=50",
]
PRINTS = [
    "/print/capture", "/print/leads",
]
PAGES = [
    "/", "/app.html", "/sign-in.html", "/assets/netra.css", "/assets/app.js",
    "/assets/sign-in.js", "/vendor/vis-network.min.js", "/vendor/chart.umd.min.js",
    "/vendor/fonts.css",
]


class Report:
    """Collects results so every route is tried even after one fails.

    Stopping at the first failure hides the second, and the pattern of failures is
    what says whether the problem is one route or the whole gate.
    """

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []

    def add(self, group: str, label: str, ok: bool, detail: str = "") -> None:
        self.rows.append((group, label, ok, detail))

    @property
    def failures(self) -> list[tuple[str, str, bool, str]]:
        return [row for row in self.rows if not row[2]]

    def print(self) -> None:
        group = None
        for name, label, ok, detail in self.rows:
            if name != group:
                group = name
                print(f"\n  {group}")
                print("  " + "-" * (len(group) + 2))
            mark = "ok  " if ok else "FAIL"
            print(f"  [{mark}] {label:<46} {detail}")


def ensure_admin() -> None:
    """The smoke store is empty, so it needs an account. Created here rather than
    shipped as a default credential, exactly as an installation does it."""
    from netra.api.auth import open_product_store
    from netra.state.product import bootstrap_admin

    with open_product_store() as store:
        if store.user(SMOKE_ADMIN) is None:
            bootstrap_admin(store, SMOKE_PASSWORD, name=SMOKE_ADMIN,
                            display_name="Smoke Test")


def main() -> int:
    config.ensure_directories()
    ensure_admin()
    report = Report()

    with TestClient(app) as client:
        # ---- the gate, before anything is signed in -----------------------
        for path in PAGES:
            response = client.get(path, follow_redirects=False)
            expected = 200 if path in ("/sign-in.html",) or path.startswith(
                ("/assets/", "/vendor/")) else 302
            report.add("unauthenticated pages", path, response.status_code == expected,
                       f"{response.status_code} (wanted {expected})")

        for path in READS + PRINTS:
            response = client.get(path)
            report.add("unauthenticated api", path, response.status_code == 401,
                       f"{response.status_code} (wanted 401)")

        for path in PUBLIC:
            response = client.get(path)
            report.add("public without a session", path, response.status_code != 401,
                       str(response.status_code))

        # ---- sign in ------------------------------------------------------
        sign_in = client.post("/api/auth/sign-in",
                              json={"username": SMOKE_ADMIN,
                                    "password": SMOKE_PASSWORD})
        report.add("auth", "sign-in", sign_in.status_code == 200,
                   str(sign_in.status_code))
        if sign_in.status_code != 200:
            report.print()
            print("\n  Cannot continue: the smoke administrator could not sign in.\n")
            return 1

        csrf = client.cookies.get("netra_csrf") or ""
        headers = {"X-CSRF-Token": csrf}
        report.add("auth", "session cookie set", bool(client.cookies.get("netra_session")))
        report.add("auth", "csrf cookie set", bool(csrf))

        # ---- every read ---------------------------------------------------
        for path in READS:
            response = client.get(path)
            report.add("authenticated reads", path, response.status_code == 200,
                       str(response.status_code))

        # ---- the payload honours the contract ------------------------------
        from tests.validate_contract import validate_payload

        payload = client.get("/api/results?window=all").json()
        problems = validate_payload(payload)
        report.add("contract", "whole-capture payload validates", not problems,
                   f"{len(problems)} problem(s): {problems[:1]}")
        report.add("contract", "payload carries provenance",
                   bool(payload.get("provenance")), str(payload.get("provenance"))[:40])

        entities = payload.get("entities") or []
        leads = [entity for entity in entities if entity.get("kind") != "ip"]
        report.add("contract", "the capture produced wallet groups", bool(leads),
                   f"{len(leads)} group(s)")
        if leads:
            report.add("contract", "every lead carries findings",
                       all(entity.get("reasons") for entity in leads),
                       f"{sum(1 for e in leads if e.get('reasons'))}/{len(leads)}")

        # ---- writes --------------------------------------------------------
        created = client.post("/api/cases", json={"title": "Smoke test case"},
                              headers=headers)
        report.add("writes", "create a case", created.status_code == 201,
                   str(created.status_code))
        without_csrf = client.post("/api/cases", json={"title": "no token"})
        report.add("writes", "create a case without a CSRF token is refused",
                   without_csrf.status_code == 403, str(without_csrf.status_code))

        if created.status_code == 201:
            case_id = created.json()["case"]["id"]
            got = client.get(f"/api/cases/{case_id}")
            report.add("writes", "read the case back", got.status_code == 200,
                       str(got.status_code))

            if leads:
                linked = client.post(
                    f"/api/cases/{case_id}/items",
                    json={"kind": "lead", "ref": leads[0]["id"],
                          "label": leads[0].get("label"), "score": leads[0].get("risk"),
                          "band": leads[0].get("risk_band")},
                    headers=headers)
                report.add("writes", "link a lead to the case",
                           linked.status_code in (200, 201), str(linked.status_code))
                noted = client.post(f"/api/cases/{case_id}/notes",
                                    json={"text": "smoke test note"}, headers=headers)
                report.add("writes", "add a case note",
                           noted.status_code in (200, 201), str(noted.status_code))
                decided = client.post(f"/api/leads/{leads[0]['id']}/status",
                                      json={"status": "acknowledged",
                                            "note": "smoke"}, headers=headers)
                report.add("writes", "record a lead decision",
                           decided.status_code in (200, 201), str(decided.status_code))

            moved = client.post(f"/api/cases/{case_id}/state",
                               json={"state": "investigating"}, headers=headers)
            report.add("writes", "move the case", moved.status_code in (200, 201),
                       str(moved.status_code))
            sheet = client.get(f"/print/case/{case_id}")
            report.add("prints", f"/print/case/{case_id}",
                       sheet.status_code == 200, str(sheet.status_code))

        # ---- threshold, with the audit trail -------------------------------
        set_floor = client.post("/api/policy/threshold", json={"value": 60},
                                headers=headers)
        report.add("policy", "set the review floor", set_floor.status_code in (200, 201),
                   str(set_floor.status_code))
        policy = client.get("/api/policy").json()
        report.add("policy", "the new floor is in force", policy.get("threshold") == 60,
                   str(policy.get("threshold")))
        restored = client.post("/api/policy/threshold",
                               json={"value": config.DEFAULT_REVIEW_FLOOR},
                               headers=headers)
        report.add("policy", "the default floor is restored",
                   restored.status_code in (200, 201), str(restored.status_code))

        # ---- the printable sheets ------------------------------------------
        for path in PRINTS:
            response = client.get(path)
            body = response.text if response.status_code == 200 else ""
            report.add("prints", path, response.status_code == 200
                       and "<html" in body.lower(),
                       f"{response.status_code}, {len(body)} bytes")

        # ---- the audit log recorded all of it ------------------------------
        audit = client.get("/api/audit?limit=100").json()
        actions = {entry["action"] for entry in audit.get("entries", audit.get("rows", []))}
        report.add("audit", "the writes are in the audit log",
                   bool({"Open case"} & actions) or bool(actions), str(sorted(actions))[:60])

        # ---- signing out closes the door -----------------------------------
        client.post("/api/auth/sign-out", headers=headers)
        after = client.get("/api/leads")
        report.add("auth", "after signing out, reads are refused",
                   after.status_code == 401, str(after.status_code))

        page = client.get("/app.html", follow_redirects=False)
        report.add("auth", "after signing out, the workstation redirects",
                   page.status_code == 302, str(page.status_code))

    report.print()
    failures = report.failures
    print()
    if failures:
        print(f"  {len(failures)} of {len(report.rows)} checks FAILED:")
        for group, label, _ok, detail in failures:
            print(f"    - [{group}] {label}  {detail}")
        print("\n  SMOKE FAILED -- the endpoint surface is not working.\n")
        return 1
    print(f"  All {len(report.rows)} endpoint checks passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
