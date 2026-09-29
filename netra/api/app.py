"""
The application: one process serving the API and the interface.

WHY ONE PROCESS
---------------
The target is a single air-gapped host. Serving the JSON API and the pages from the
same process means deployment is one command, there is no CORS to configure, and
there is no reverse proxy to misconfigure. On a machine that has never seen a
package registry, every moving part removed is a failure mode that cannot happen.

WHAT IS GATED
-------------
Everything. The pages, the assets, the print routes and every endpoint. A case
dossier that renders without a session is a leak, and the sign-in screen is the
only URL that answers to an anonymous request.

WHAT IS NOT HERE
----------------
The analysis. This module assembles: it resolves a session, applies a capability
check, calls a service, and audits the result. Anything that computes lives in the
layer that owns it, so a route can never quietly become the place where a rule
lives.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import (APIRouter, Body, Depends, FastAPI, File, HTTPException, Query,
                     Request, UploadFile)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from netra import config
from netra.api.auth import (Principal, admin_router, current_principal,
                            open_product_store, require, require_csrf, router as auth_router)
from netra.operations.jobs import JobStore

app = FastAPI(
    title="NETRA",
    version=config.ENGINE_VERSION,
    description=(
        "Network-Enhanced Transaction Risk Analysis. Ingests bulk Bitcoin and "
        "network metadata, fuses the two layers, and produces ranked, explainable "
        "investigative leads with a monitoring history. Offline by design."
    ),
    docs_url=None,          # the interactive console is an unauthenticated surface
    redoc_url=None,
    openapi_url=None,
)

jobs = JobStore()


def _store():
    """The analysis store, opened per request (see auth.open_product_store for why)."""
    from netra.state.store import MonitoringStore
    if not config.STATE_DB.exists():
        raise HTTPException(status_code=409,
                            detail="no analysis yet -- run an ingest first")
    return MonitoringStore(config.STATE_DB)


def _payload(window: str | None, *, default: str = "latest") -> dict[str, Any]:
    """The contract payload, with a state problem reported as state.

    `payload_for` raises ValueError for the things an operator can actually fix --
    no capture analysed yet, or a batch number that does not exist -- and letting
    that reach the client as a 500 with a stack trace tells them the software is
    broken when the answer is "you have not analysed anything yet". 409 for an
    empty store, 404 for a named batch that is not there.
    """
    from netra.operations.payload import payload_for

    with _store() as store:
        try:
            return payload_for(store, window, default=default)
        except ValueError as exc:
            message = str(exc)
            code = 404 if "no such batch" in message else 409
            raise HTTPException(status_code=code, detail=message) from exc


def _live_threshold() -> int:
    with open_product_store() as store:
        return store.threshold()


def _champion_version() -> str | None:
    with open_product_store() as store:
        champion = store.champion()
        return champion["version"] if champion else None


def _provenance(principal: Principal | None = None) -> dict[str, Any]:
    """Engine, model and capture versions, stamped on every payload and report.

    Provenance is not decoration: an output that cannot say which model and which
    engine produced it cannot be compared with one produced after either moved.
    """
    return {
        "engine_version": config.ENGINE_VERSION,
        "schema_version": config.SCHEMA_VERSION,
        "model_version": _champion_version(),
        "threshold": _live_threshold(),
        "actor": principal.name if principal else None,
    }


# ==========================================================================
# Data routes. Every one of them requires a session, and the capability that
# matches what they expose.
# ==========================================================================
data = APIRouter(prefix="/api", tags=["data"])


@app.get("/api/auth/version", tags=["auth"])
def version() -> dict[str, Any]:
    """Engine and schema version, and nothing else.

    Public deliberately: it carries no state, and it is the one thing somebody
    locked out of the application still needs to report a fault.
    """
    return {"engine_version": config.ENGINE_VERSION,
            "schema_version": config.SCHEMA_VERSION,
            "product": "NETRA"}


@data.get("/health")
def health(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """What state exists, in one call.

    Includes the loud flag the interface needs: `model_loaded`. Without it, a
    missing model produces a working-looking screen reporting zero findings, which
    is the most dangerous state this product can reach.
    """
    state: dict[str, Any] = {
        "status": "ok",
        "store_exists": config.STATE_DB.exists(),
        "models": {
            "risk": (config.MODELS_DIR / "risk.joblib").exists(),
            "anomaly": (config.MODELS_DIR / "anomaly.joblib").exists(),
            "metrics": (config.MODELS_DIR / "metrics.json").exists(),
        },
        "provenance": _provenance(principal),
    }
    state["model_loaded"] = state["models"]["risk"]
    if state["store_exists"]:
        with _store() as store:
            state["store"] = store.summary()
            latest = store.last_window()
            if latest is not None:
                state["latest_batch"] = {"id": latest.window_id, "label": latest.label,
                                         "end": latest.end_ts, "transactions": latest.n_tx}
    return state


@data.get("/windows")
def windows(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with _store() as store:
        records = store.windows()
        return {
            "batches": [
                {"id": record.window_id, "label": record.label, "start": record.start_ts,
                 "end": record.end_ts, "transactions": record.n_tx,
                 "entities": record.n_entities, "index": position + 1,
                 "total": len(records)}
                for position, record in enumerate(records)
            ],
            "store": store.summary(),
            "provenance": _provenance(principal),
        }


@data.get("/results")
def results(window: str | None = Query(default=None),
            principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """The contract payload for one batch, or `window=all` for the whole capture.

    `payload_for` validates against schemas/netra.schema.json before it returns, so
    what goes out here has already been checked against the contract the reports
    and the exports are rendered from. The check is on the payload as SERVED, which
    is the only place it means anything.
    """
    payload = _payload(window, default="latest")
    payload["provenance"] = _provenance(principal)
    return payload


@data.get("/events")
def events(window: int | None = Query(default=None, ge=1),
           principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with _store() as store:
        frame = store.events(window)
        if frame.empty:
            return {"events": [], "counts": {}}
        records = json.loads(frame.to_json(orient="records"))
        counts: dict[str, int] = {}
        for record in records:
            counts[record["type"]] = counts.get(record["type"], 0) + 1
        return {"events": records, "counts": counts}


@data.get("/leads")
def leads(status: str | None = None, band: str | None = None,
          principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """The queue, with every key resolved to a current identity.

    A group that was merged into another keeps its alert row as a historical record
    but its key no longer names a group, so a client that followed it would ask for a
    dossier that does not exist. Resolution belongs here, once.
    """
    with _store() as store:
        frame = store.alerts(status)
        if frame.empty:
            return {"leads": [], "open": 0, "threshold": _live_threshold()}
        records = json.loads(frame.to_json(orient="records"))
        for record in records:
            record["entity_key"] = store.canonical(str(record["entity_key"]))
            if band:
                record["band"] = config.band_for(int(record["current_risk"]))
        if band:
            records = [r for r in records if r.get("band") == band]
        return {
            "leads": records,
            "open": int(sum(1 for r in records
                            if not str(r["status"]).startswith("closed"))),
            "threshold": _live_threshold(),
        }


class LeadStatus(BaseModel):
    status: str
    assignee: str | None = None
    note: str | None = None


@data.post("/leads/{entity_key}/status")
def set_lead_status(entity_key: str, update: LeadStatus,
                    principal: Principal = Depends(require("disposition")),
                    _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Record the analyst's decision, and -- when it is a closure -- learn from it.

    The disposition is written twice on purpose: once as alert lifecycle on the
    analysis side, and once as a LABEL. They serve different readers. The lifecycle
    is what the queue shows; the label is what the next model trains on, and it
    carries a strength that says how much it should be trusted.
    """
    from netra.state.store import ALERT_STATUSES

    if update.status not in ALERT_STATUSES:
        raise HTTPException(status_code=400,
                            detail=f"unknown status '{update.status}' -- expected one "
                                   f"of {list(ALERT_STATUSES)}")
    with _store() as store:
        canonical = store.canonical(entity_key)
        store.set_alert_status(canonical, update.status, update.assignee, update.note)
    if update.status.startswith("closed"):
        with open_product_store() as store:
            store.add_disposition(canonical, update.status, actor=principal.name,
                                  note=update.note)
            store.audit(actor=principal.name, role=principal.role, action="Disposition",
                        object_type="lead", object_id=canonical,
                        detail=f"{update.status}" + (f" -- {update.note}" if update.note else ""))
    return {"entity_key": canonical, "status": update.status}


@data.get("/monitoring")
def monitoring(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with _store() as store:
        return {"batches": [{"id": w.window_id, "label": w.label} for w in store.windows()]}


@data.get("/glossary")
def glossary(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Plain-language explanations, served from one place.

    The same sentences appear on the screen, in the printed report and in the case
    dossier. Three hand-written copies is three chances to drift, and the copy that
    drifts is the one quoted back in a briefing.
    """
    from netra.models.explainers import glossary as build
    return build()


@data.get("/metrics")
def metrics(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    path = config.MODELS_DIR / "metrics.json"
    if not path.exists():
        # Null means "not measured". It must never mean "put a placeholder here".
        return {"evaluated_on": "model not trained", "model_loaded": False}
    return {**json.loads(path.read_text(encoding="utf-8")), "model_loaded": True}


# ==========================================================================
# Ingest. The gate runs before anything expensive, and its report is the point:
# the operator's question is "did you use my data?", and an answer that arrives
# after a two-minute analysis is not an answer.
# ==========================================================================
@data.post("/ingest")
async def ingest(file: UploadFile = File(...),
                 principal: Principal = Depends(require("view")),
                 _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    from netra.data.ingest import load_capture

    config.ensure_directories()
    name = Path(file.filename or "capture.csv").name
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "._-") or "capture.csv"
    target = config.UPLOAD_DIR / safe
    payload = await file.read()
    target.write_bytes(payload)

    digest = hashlib.sha256(payload).hexdigest()[:16]
    try:
        frame, report = load_capture(target)
    except ValueError as exc:
        with open_product_store() as store:
            store.audit(actor=principal.name, role=principal.role, action="Ingest",
                        object_type="file", object_id=safe, evidence_hash=digest,
                        detail=str(exc)[:300], result="REFUSED")
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    with open_product_store() as store:
        store.audit(actor=principal.name, role=principal.role, action="Ingest",
                    object_type="file", object_id=safe, evidence_hash=digest,
                    detail=f"{report.accepted} usable of {report.total} rows")
    return {"stored_as": str(target), "evidence_hash": digest,
            "report": report.as_dict()}


class AnalyzeRequest(BaseModel):
    dataset: str | None = None
    label: str | None = None


@data.post("/analyze", status_code=202)
def analyze(request: AnalyzeRequest | None = None,
            principal: Principal = Depends(require("view")),
            _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Start a windowed replay in the background.

    Returns a job id immediately. A full replay takes tens of seconds; holding the
    request open that long means the browser times out and the analyst watches a
    spinner with no explanation -- so the work is a job and the page polls it.
    """
    request = request or AnalyzeRequest()
    dataset = Path(request.dataset) if request.dataset else config.DATA_DIR / "transactions.csv"
    if not dataset.exists():
        raise HTTPException(status_code=404, detail=f"no such capture: {dataset}")

    actor = principal.name
    def work(job_id: str) -> dict[str, Any]:
        from netra.operations.pipeline import replay

        digest = hashlib.sha256(dataset.read_bytes()).hexdigest()[:16]
        jobs.log(job_id, f"capture: {dataset.name} (sha256 {digest})")
        result = replay(store_path=config.STATE_DB, dataset=dataset,
                        data_dir=config.DATA_DIR, models_dir=config.MODELS_DIR,
                        reset=True)
        for position, batch in enumerate(result["windows"], start=1):
            jobs.log(job_id, f"[{position}/{len(result['windows'])}] {batch['label']}: "
                             f"{batch['transactions']} tx -> {batch['entities']} groups, "
                             f"{batch['events']} events")
            jobs.progress(job_id, position / max(len(result["windows"]), 1))
        jobs.log(job_id, "preparing the whole-capture view")
        _payload("all", default="all")
        jobs.log(job_id, "ready")
        with open_product_store() as store:
            store.record_run(job_id, kind="analysis", input_name=dataset.name,
                             input_hash=digest, input_bytes=dataset.stat().st_size,
                             rows_read=result["store"]["addresses"],
                             rows_usable=result["store"]["windows"],
                             model_version=_champion_version(),
                             started_at=result.get("started_at", ""),
                             finished_at=result.get("finished_at", ""),
                             output_fingerprint=digest, actor=actor)
        return {"store": result["store"],
                "time_to_detection": result["time_to_detection"]}

    job = jobs.submit("analysis", work)
    return {"job_id": job.id, "status": job.status, "poll": f"/api/job/{job.id}"}


@data.get("/job/{job_id}")
def job_status(job_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no such job: {job_id}")
    return job.as_dict()


# ==========================================================================
# Cases, dispositions, canaries, policy, audit. The work layer.
# ==========================================================================
work = APIRouter(prefix="/api", tags=["work"])


class NewCase(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    severity: str | None = None


@work.get("/cases")
def list_cases(state: str | None = None,
               principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with open_product_store() as store:
        return {"cases": store.cases(state)}


@work.post("/cases", status_code=201)
def create_case(payload: NewCase,
                principal: Principal = Depends(require("case_edit")),
                _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        case_id = store.create_case(payload.title, owner=principal.name,
                                    severity=payload.severity)
        store.audit(actor=principal.name, role=principal.role, action="Open case",
                    object_type="case", object_id=case_id, detail=payload.title)
        return {"case": store.case(case_id)}


@work.get("/cases/{case_id}")
def get_case(case_id: str, principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with open_product_store() as store:
        case = store.case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail=f"no such case: {case_id}")
        return {"case": case}


class CaseItem(BaseModel):
    kind: str = "lead"
    ref: str
    label: str | None = None
    score: int | None = None
    band: str | None = None


@work.post("/cases/{case_id}/items", status_code=201)
def add_case_item(case_id: str, payload: CaseItem,
                  principal: Principal = Depends(require("case_edit")),
                  _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        if store.case(case_id) is None:
            raise HTTPException(status_code=404, detail=f"no such case: {case_id}")
        store.add_case_item(case_id, payload.kind, payload.ref, label=payload.label,
                            score=payload.score, band=payload.band, actor=principal.name)
        store.audit(actor=principal.name, role=principal.role, action="Add to case",
                    object_type="case", object_id=case_id,
                    detail=f"{payload.kind} {payload.ref}")
        return {"case": store.case(case_id)}


class CaseNote(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@work.post("/cases/{case_id}/notes", status_code=201)
def add_case_note(case_id: str, payload: CaseNote,
                  principal: Principal = Depends(require("case_edit")),
                  _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        store.add_case_note(case_id, payload.text, principal.name)
        store.audit(actor=principal.name, role=principal.role, action="Case note",
                    object_type="case", object_id=case_id)
        return {"case": store.case(case_id)}


class CaseState(BaseModel):
    state: str


@work.post("/cases/{case_id}/state")
def set_case_state(case_id: str, payload: CaseState,
                   principal: Principal = Depends(require("case_edit")),
                   _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    if payload.state not in ("open", "investigating", "referred", "closed"):
        raise HTTPException(status_code=400,
                            detail="state must be open, investigating, referred or closed")
    with open_product_store() as store:
        store.set_case_state(case_id, payload.state, principal.name)
        store.audit(actor=principal.name, role=principal.role, action="Case state",
                    object_type="case", object_id=case_id, detail=payload.state)
    return {"case_id": case_id, "state": payload.state}


@work.get("/dispositions")
def list_dispositions(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """What the model learns from, with the strength of each label.

    Served as the labelled set rather than a list of decisions, because the screen
    that shows it has to be able to say how much of this is evidence and how much is
    a judgement made in minutes.
    """
    with open_product_store() as store:
        return {"dispositions": store.dispositions(), "counts": store.disposition_counts()}


@work.get("/canaries")
def list_canaries(unlabelled_only: bool = False,
                  principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with open_product_store() as store:
        return {"canaries": store.canaries(),
                "observations": store.canary_observations(unlabelled_only=unlabelled_only)}


class NewCanary(BaseModel):
    address: str = Field(min_length=26, max_length=90)
    purpose: str | None = None


@work.post("/canaries", status_code=201)
def add_canary(payload: NewCanary,
               principal: Principal = Depends(require("configure")),
               _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    with open_product_store() as store:
        store.add_canary(payload.address, owner=principal.name, purpose=payload.purpose)
        store.audit(actor=principal.name, role=principal.role, action="Register canary",
                    object_type="canary", object_id=payload.address[:20])
        return {"canaries": store.canaries()}


class CanaryLabel(BaseModel):
    label: str


@work.post("/canaries/observations/{observation_id}/label")
def label_canary(observation_id: int, payload: CanaryLabel,
                 principal: Principal = Depends(require("canary_label")),
                 _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Label one interaction with a canary.

    This is the highest-value label in the system: nobody legitimate has a reason to
    pay an unused address, so what arrived is a sample of live adversary behaviour.
    """
    if payload.label not in ("suspicious", "benign", "unknown"):
        raise HTTPException(status_code=400,
                            detail="label must be suspicious, benign or unknown")
    with open_product_store() as store:
        store.label_canary_observation(observation_id, payload.label, principal.name)
        store.audit(actor=principal.name, role=principal.role, action="Label canary",
                    object_type="observation", object_id=str(observation_id),
                    detail=payload.label)
        return {"observations": store.canary_observations()}


@work.get("/policy")
def policy(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with open_product_store() as store:
        return {"threshold": store.threshold(), "history": store.threshold_history(),
                "default": config.DEFAULT_REVIEW_FLOOR,
                "bands": [{"band": name, "from": threshold}
                          for threshold, name in config.BAND_THRESHOLDS]}


@work.get("/policy/simulate")
def simulate(floor: int = Query(ge=0, le=100),
             principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """What a review floor would produce, from the measured score distribution.

    The point of the screen: a buyer chooses their workload and sees the precision
    they are trading for it, rather than being told a number and asked to trust it.
    """
    # Through the cache, not around it: this screen is the same whole-capture view
    # the map already has, and rebuilding it took 25 seconds to count a column that
    # was sitting in the cache file.
    payload = _payload("all", default="all")
    scored = [int(entity["risk"]) for entity in payload["entities"]
              if entity.get("kind") != "ip"]
    above = [score for score in scored if score >= floor]
    # Precision is estimated from the calibration curve, not asserted from the band
    # name: at floor f, the expected precision is the mean predicted probability
    # among the groups above it.
    confidences = [float(entity.get("confidence") or 0)
                   for entity in payload["entities"]
                   if entity.get("kind") != "ip" and int(entity["risk"]) >= floor]
    return {
        "floor": floor,
        "groups_examined": len(scored),
        "leads": len(above),
        "share_of_groups": round(len(above) / len(scored), 4) if scored else 0.0,
        "estimated_precision": round(sum(confidences) / len(confidences), 4)
                               if confidences else None,
        "note": ("estimated from the model's own calibrated probabilities on this "
                 "capture; it is not a measured outcome. Only confirmed cases can "
                 "establish precision on real data."),
    }


class Threshold(BaseModel):
    value: int = Field(ge=0, le=100)
    reason: str | None = None


@work.post("/policy/threshold")
def set_threshold(payload: Threshold,
                  principal: Principal = Depends(require("threshold")),
                  _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Write the floor, audit it, and state that it applies from the next analysis.

    A threshold silently applied to the current screen would mean the numbers on
    it changed while somebody was reading them.
    """
    with open_product_store() as store:
        previous = store.threshold()
        store.set_threshold(payload.value, principal.name, payload.reason)
        store.audit(actor=principal.name, role=principal.role, action="Set threshold",
                    object_type="policy", object_id=str(payload.value),
                    detail=f"{previous} -> {payload.value}"
                           + (f" -- {payload.reason}" if payload.reason else ""))
        return {"threshold": payload.value, "previous": previous,
                "applies_to": "future analyses"}


@work.get("/audit")
def audit_log(actor: str | None = None, action: str | None = None,
              limit: int = Query(default=200, ge=1, le=2000),
              principal: Principal = Depends(require("audit_read"))) -> dict[str, Any]:
    """The append-only log. Read-only here, and read-only in the database.

    An auditor sees every entry; everybody else sees their own actions, which is
    enough to answer "what did I do" without turning the log into a way to watch
    colleagues.
    """
    with open_product_store() as store:
        scoped = None if principal.role in ("admin", "auditor") else principal.name
        entries = store.audit_entries(actor=actor or scoped, action=action, limit=limit)
        return {"entries": entries, "immutable": True,
                "scope": "all" if scoped is None else "own actions"}


@work.get("/models")
def models(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    with open_product_store() as store:
        return {"models": store.models(), "champion": store.champion(),
                "live": _champion_version()}


@work.post("/models/{version}/promote")
def promote(version: str, principal: Principal = Depends(require("manage_models")),
            _csrf: Principal = Depends(require_csrf)) -> dict[str, Any]:
    """Make a model the champion. A rollback is the same call with the older version,
    so one path serves both and neither can behave differently from the other."""
    with open_product_store() as store:
        if not any(model["version"] == version for model in store.models()):
            raise HTTPException(status_code=404, detail=f"no such model version: {version}")
        store.promote_model(version, principal.name)
        store.audit(actor=principal.name, role=principal.role, action="Promote model",
                    object_type="model", object_id=version)
        return {"champion": store.champion()}


@work.get("/runs")
def runs(principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    """Every analysis, with the capture hash and model version that produced it."""
    with open_product_store() as store:
        return {"runs": store.runs()}


# ==========================================================================
# Reports. Server-rendered A4, printed by the browser: vector text and vector
# charts, no PDF library, and nothing to install on a host with no registry.
# ==========================================================================
reports = APIRouter(prefix="/print", tags=["reports"])


@reports.get("/capture", response_class=HTMLResponse)
def print_capture(window: str | None = Query(default=None),
                  principal: Principal = Depends(require("export"))) -> HTMLResponse:
    from netra.report.sheets import dataset_report_html

    payload = _payload(window, default="all")
    payload["provenance"] = _provenance(principal)
    _audit_report(principal, "Whole-capture report", "capture", window or "all",
                  payload["meta"].get("generated_at"))
    return HTMLResponse(content=dataset_report_html(payload))


@reports.get("/leads", response_class=HTMLResponse)
def print_leads(window: str | None = Query(default=None),
                principal: Principal = Depends(require("export"))) -> HTMLResponse:
    from netra.models.explainers import ANOMALY_EXPLAINERS
    from netra.report.sheets import anomalies_report_html

    payload = _payload(window, default="all")
    payload["provenance"] = _provenance(principal)
    _audit_report(principal, "Lead report", "capture", window or "all",
                  payload["meta"].get("generated_at"))
    return HTMLResponse(content=anomalies_report_html(payload, ANOMALY_EXPLAINERS))


@reports.get("/case/{case_id}", response_class=HTMLResponse)
def print_case(case_id: str,
               principal: Principal = Depends(require("export"))) -> HTMLResponse:
    """A case dossier, assembled from the case, its leads and the capture's evidence."""
    from netra.report.sheets import case_dossier_html as render_case

    with open_product_store() as store:
        case = store.case(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail=f"no such case: {case_id}")
    # A case dossier is still worth printing when the analysis it referred to is
    # gone: the case, its notes and its audit trail are the organisation's record,
    # and they outlive the capture. So a missing payload is a stated absence, not
    # an error.
    try:
        payload = _payload(None, default="all")
    except HTTPException:
        payload = None
    _audit_report(principal, "Case report", "case", case_id, None)
    return HTMLResponse(content=render_case(case, payload, _provenance(principal)))


@reports.get("/campaign/{campaign_id}", response_class=HTMLResponse)
def print_campaign(campaign_id: str,
                   principal: Principal = Depends(require("export"))) -> HTMLResponse:
    """The victims of one attacker: what a bank or an exchange asks for first."""
    from netra.report.sheets import campaign_report_html

    payload = _payload(None, default="all")
    campaign = next((item for item in payload.get("campaigns", [])
                     if item["id"] == campaign_id), None)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"no such campaign: {campaign_id}")
    payload["provenance"] = _provenance(principal)
    _audit_report(principal, "Campaign report", "campaign", campaign_id, None)
    return HTMLResponse(content=campaign_report_html(campaign, payload))


def _audit_report(principal: Principal, what: str, kind: str, ref: str,
                  generated_at: str | None) -> None:
    """Every export is an audited event.

    A report is the artefact most likely to leave the building, so who produced it
    and from which model version is exactly the question asked afterwards.
    """
    with open_product_store() as store:
        store.audit(actor=principal.name, role=principal.role, action=what,
                    object_type=kind, object_id=str(ref),
                    model_version=_champion_version(),
                    detail=f"generated {generated_at or 'now'}")


# ==========================================================================
# The interface, and the gate in front of it.
# ==========================================================================
# WHAT IS PUBLIC, AND WHY THAT IS NOT A HOLE
# ------------------------------------------
# The sign-in page, and the CSS and JavaScript it needs to render. Those files
# contain no data: they are the code that draws a form. Everything that carries a
# fact -- every page of the application, every endpoint, every report -- requires a
# session. A stylesheet is not intelligence; a case dossier is.
#
# `/api/auth/redeem-reset` is public because of who uses it: somebody who cannot
# sign in. It was left off this list, which meant the recovery path answered 401 to
# the only person who could ever need it -- a reset that cannot be redeemed is not a
# reset. It is safe to expose because it authenticates with a single-use token that
# an administrator issued out of band and that expires in 30 minutes, and because
# redemption writes to the audit log both when it succeeds and when it is refused.
PUBLIC_PREFIXES: tuple[str, ...] = ("/assets/", "/vendor/", "/favicon.ico")
PUBLIC_PATHS: tuple[str, ...] = ("/sign-in.html", "/api/auth/sign-in",
                                 "/api/auth/redeem-reset", "/api/auth/version")


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES)


@app.middleware("http")
async def require_a_session(request: Request, call_next):
    """Gate every request that is not explicitly public.

    A redirect rather than a JSON error for a page request, because a browser that
    navigates to a page and receives `{"detail":"..."}` shows that text to the
    analyst. An API caller gets the status code and the reason, which is what a
    caller can act on.
    """
    path = request.url.path
    if _is_public(path) or request.method == "OPTIONS":
        return await call_next(request)

    # The cookie name comes from the auth module, not a literal: two places
    # knowing the name is two places to change it wrong.
    from netra.state.product import SESSION_COOKIE
    with open_product_store() as store:
        cookie = request.cookies.get(SESSION_COOKIE)
        session, reason = store.session(cookie)

    if session is None:
        if path.startswith("/api") or path.startswith("/print"):
            return JSONResponse(status_code=401,
                                content={"detail": reason, "reason": reason})
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=f"/sign-in.html?reason={reason}", status_code=302)
    return await call_next(request)


app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(data)
app.include_router(work)
app.include_router(reports)


class GatedStatic(StaticFiles):
    """Static files with no-cache headers.

    There is no build step: the browser loads the files on disk. That makes the
    browser's own heuristics the only thing between an edited stylesheet and the
    screen, which during development is a trap -- it has already cost this project
    an hour of chasing a fix that was correct on disk. `no-cache` means
    "revalidate", which on localhost costs nothing.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


if (config.WEB_DIR / "sign-in.html").exists():
    app.mount("/", GatedStatic(directory=str(config.WEB_DIR), html=True), name="web")


def main() -> int:
    """Entry point for `python -m netra.api.app`."""
    import uvicorn

    config.ensure_directories()
    uvicorn.run(app, host="127.0.0.1", port=8000)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
