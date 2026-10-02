# NETRA

Network-Enhanced Transaction Risk Analysis

Team Vortex · SIH 2026

NETRA is an offline, evidence-first workstation for financial intelligence and cybercrime investigation teams. It ingests blockchain transaction data, resolves wallet and entity identities, scores suspicious activity, and presents ranked investigative leads with explainable evidence that can be reviewed by an analyst without leaving the machine.

This repository contains the complete product build: the ingestion pipeline, identity and graph logic, model training, review workflow, authentication and authorization, reporting layer, and the static web interface.

---

## 1. Project purpose

The problem is not simply "detect anomalies." The harder problem is this:

- transaction files arrive in messy, inconsistent formats
- wallet addresses are reused, split, merged, or represented in multiple aliases
- investigators must work with incomplete data and uncertain labels
- outputs must be explainable, auditable, and safe for sensitive environments
- the environment may be air-gapped or restricted, with no access to external APIs or cloud services

NETRA addresses this by combining a structured ingestion pipeline, graph-based entity resolution, risk modelling, and a review-ready interface designed for an analyst workstation rather than a generic dashboard.

The product is intentionally built to be:

- offline-first
- explainable
- deterministic where possible
- auditable
- usable on a real investigation workstation without network connectivity

---

## 2. What the product does

NETRA helps teams answer three questions:

1. What transaction data did we ingest, and what did we infer from it?
2. Which entities or wallet clusters look suspicious and why?
3. Which cases or leads should a reviewer inspect next, based on risk, attribution, and context?

At a high level, the workflow is:

- ingest a transaction capture from CSV or similar source data
- normalize and validate fields
- detect malformed or missing information
- resolve clusters, flow relationships, and network structure
- compute behavioural and graph-based features
- score risk and anomaly using trained models
- explain the strongest drivers behind each lead
- store findings, events, and audit history for later review
- expose a web application and printable reports to analysts

---

## 3. Architecture overview

The codebase is organized as a modular Python product rather than a single script.

| Area | Purpose |
|---|---|
| netra/ | Core product code: ingestion, identity, features, models, API, state, reporting |
| web/ | Static UI shell, HTML, CSS, JavaScript, and bundled assets |
| schemas/ | Payload contract and validation schema |
| tests/ | Regression tests, authentication tests, state checks, contract validation |
| data/ | Generated and sample dataset inputs |
| models/ | Trained model artifacts and metrics |
| out/ | Runtime state, SQLite history, and analysis outputs |
| uploads/ | User-uploaded captures for review or analysis |
| tools/ | Acceptance, validation, and data-shape utilities |
| docs/ | Project design, references, and supporting documentation |

### Main runtime layers

- Data layer: ingestion, normalization, quality gates, unit conversion
- Identity layer: entity clustering, wallet grouping, address resolution
- Correlation layer: relationship and flow analysis across windows
- Feature layer: risk and anomaly features built from graph and transaction attributes
- Model layer: trained risk models, anomaly detectors, attribution, metrics, drift tracking
- State layer: SQLite-backed store for users, sessions, windows, analysis, and audit records
- API layer: FastAPI endpoints protecting the product and serving data to the UI
- UI layer: static web application for investigation, review, and reporting

---

## 4. Repository layout

```text
.
├── README.md
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── run.sh
├── run.bat
├── Makefile
├── tasks.py
├── data/
│   ├── ground_truth_addresses.csv
│   ├── ground_truth_entities.csv
│   ├── ground_truth_transactions.csv
│   ├── transactions.csv
│   └── windows/
├── docs/
├── models/
│   ├── anomaly.joblib
│   ├── risk.joblib
│   ├── feature_columns.json
│   ├── metrics.json
│   └── ENVIRONMENT.txt
├── netra/
│   ├── api/
│   ├── correlate/
│   ├── data/
│   ├── features/
│   ├── identify/
│   ├── models/
│   ├── operations/
│   ├── report/
│   ├── state/
│   └── work/
├── schemas/
│   └── netra.schema.json
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_contract.py
│   ├── test_feedback.py
│   ├── test_ingest.py
│   ├── test_state.py
│   └── validate_contract.py
├── tools/
│   ├── acceptance.py
│   └── foreign_shape.py
├── uploads/
├── web/
├── wheels/
└── out/
```

---

## 5. Core design principles

### Offline by design

NETRA is designed to work on an air-gapped workstation. It does not depend on remote services during normal operation. The code is checked for external references, and the project includes a dedicated offline validation workflow.

### Explainability before score

This project is not a black-box classifier. Every flagged lead is paired with supporting evidence, explanations, and a transparent confidence model. The tool is intended to help a human analyst understand why an entity or wallet cluster was scored highly.

### Audited state

The system keeps structured state for:

- users and authentication state
- sessions and access control
- windows and batch analysis history
- cases and review outcomes
- model versions and scoring provenance
- audit logs for security-sensitive actions

### Review-driven model improvement

Analyst decisions can be used to train challenger models without silently replacing the live champion model. This keeps the product aligned with the principle that investigative judgments remain human-controlled.

---

## 6. Technical stack

The project is built using Python with the main runtime libraries pinned in requirements.txt.

### Core libraries

- Python 3.10
- pandas
- numpy
- scipy
- scikit-learn
- networkx
- joblib
- FastAPI
- uvicorn
- jsonschema
- pytest

### Web stack

The UI is a static, no-build frontend served alongside the API by FastAPI. This keeps deployment simple for an offline environment.

---

## 7. Data and modelling flow

### 7.1 Ingestion

The ingestion layer reads raw transaction capture files, normalizes field names, handles missing values, infers amounts and timestamps, and classifies the quality of each row. This is intentionally conservative: rows that cannot be interpreted are recorded with a reason instead of silently disappearing.

Key behaviour:

- supports multiple header aliases
- detects satoshi vs BTC values
- handles timezone ambiguity
- reports absent fields and their cost to analysis
- preserves an honest record of what was transformed

### 7.2 Entity and graph analysis

The system identifies entities and wallet clusters, then builds relationships for:

- connected wallet groups
- transfer flows
- co-spend and shared-entity patterns
- suspicious cycles, bursts, or campaign-style propagation

This creates the structure over which features and suspicious behaviour are derived.

### 7.3 Feature generation

Feature engineering combines transactional, graph, and behavioural signals. The project calculates risk and anomaly features from the entity history and wallet interactions rather than relying on a single raw metric.

### 7.4 Model training

The model layer trains and validates:

- a risk model for lead scoring
- an anomaly detector for outlier-like behaviour
- explainers and attribution logic
- metrics and drift references used for review and comparison

Model artifacts are stored in models/ and are versioned with environment metadata.

### 7.5 Reporting

The product emits investigation-ready outputs such as:

- lead lists and score bands
- entity-level explanations
- evidence routing and attribution
- printable report documents
- a contract-validated payload for the UI and API

---

## 8. Runtime entry points

The main command runner is tasks.py. It provides a single cross-platform interface for all common workflows.

### Quick start

```bash
python tasks.py admin
python tasks.py serve 8000
```

### One-command startup

Linux/macOS:

```bash
./run.sh
```

Windows:

```bat
run.bat
```

The startup scripts create missing directories, prepare dependencies when needed, generate required data if missing, run the replay pipeline, and serve the application.

---

## 9. Tasks and commands

These are the primary repository commands:

```bash
python tasks.py gen         # generate synthetic capture data
python tasks.py train       # train models and write metrics/artifacts
python tasks.py replay      # run windowed pipeline into state/output store
python tasks.py payload     # build a schema-validated payload
python tasks.py drift       # compare batches with the training distribution
python tasks.py admin       # create the first administrator account
python tasks.py accept FILE # read a real capture and report what was inferred
python tasks.py retrain     # train a challenger model from analyst labels
python tasks.py serve 8000  # start the API + web app on a local port
python tasks.py smoke       # fast end-to-end validation run
python tasks.py offline     # validate the project is offline-safe
python tasks.py test        # run the full pytest suite
python tasks.py clean       # remove generated runtime state
```

### The acceptance workflow

The project is intentionally built around real-world ingestion quality checks. Instead of assuming the bundled demo data is sufficient, the acceptance path reads a user-supplied file and reports:

1. which fields were mapped
2. what rows were excluded and why
3. whether units were detected and converted
4. what expected fields were missing
5. what leads or scores were produced from the file

This is the path a real analyst or evaluator would use when testing their own capture.

```bash
python tasks.py accept uploads/your_capture.csv
python tasks.py accept uploads/your_capture.csv --analyse
```

---

## 10. Setup and environment

### Prerequisites

- Python 3.10
- pip
- access to a local terminal
- optional: Docker / Docker Compose for container deployment

### Install dependencies

```bash
pip install -r requirements.txt
```

If you are working in an air-gapped environment, the repo also includes a wheels/ directory pattern for offline installation.

### Create the first administrator

On the first run, create the admin account explicitly:

```bash
python tasks.py admin
```

This stores the password as a secure local credential rather than shipping any default credential in the repository.

---

## 11. Application and API

The backend is served by FastAPI and exposes routes for:

- health and provenance checks
- windows and batch history
- result payloads
- events and alerts
- lead retrieval and filtering
- authentication and session management
- upload and reporting actions

The application is intentionally served from a single process instead of splitting API and UI into separate services. This reduces deployment complexity and is better aligned with a local evidence workstation.

### Main URLs

Once running:

- Sign-in: http://localhost:8000/sign-in.html
- Dashboard / workstation: http://localhost:8000/app.html
- API docs: http://localhost:8000/docs

The project disables the public OpenAPI docs endpoint for security reasons, so the app is intentionally constrained in a way that matches the air-gapped deployment model.

---

## 12. Security and access model

The application includes a controlled authentication and authorization model.

Key security properties:

- no default shipped admin password
- local password entry rather than committed credentials
- server-side sessions
- CSRF protection on write routes
- role-based capability checks for actions
- lockout behaviour after repeated failed login attempts
- reset token system for recovery without external identity infrastructure

This is crucial because the system is meant to operate in a secure environment where a user account is an operational control, not a convenience feature.

---

## 13. Offline and compliance constraints

NETRA is designed to remain usable when the machine is disconnected from the internet.

The repository includes an offline validation path:

```bash
python tasks.py offline
```

This check is intended to confirm that the shipped files do not include external references or network-dependent assets. In a security-sensitive or air-gapped environment, this is a project requirement rather than an optional extra.

---

## 14. Validation and testing

The project is accompanied by a meaningful test suite and validation workflow.

### Included checks

- contract validation against the payload schema
- authentication and authorization tests
- state and audit integrity tests
- ingestion quality tests
- end-to-end smoke tests covering generation, training, replay, and API checks

Typical validation commands:

```bash
python tasks.py smoke
python tasks.py test
```

The smoke workflow validates the project along the same stages a clean checkout would use: generate data, train, replay, validate contract, and exercise API behaviour.

---

## 15. Deployment notes

### Local workstation

This project is best understood as a single-host investigation workstation. It is designed to be run locally and managed by the operator, not exposed as a multi-user public service.

### Docker deployment

The repository includes Docker support for a containerised deployment path, but the product logic remains rooted in the same local-first design decisions.

---

## 16. Project status

NETRA is implemented as a complete product-level investigation platform with these core capabilities:

- ingestion and quality reporting
- identity and graph-based resolution
- feature engineering and risk scoring
- anomaly detection
- model training and challenger model workflow
- role-based authentication
- secure local state management
- analyst-friendly web interface
- report generation and evidence export

The repository is structured as a real engineering build rather than a mock prototype, and the workflow is designed to support evaluation with real capture files, not just bundled demo data.

---

## 17. Recommended first run

For a fresh setup, use:

```bash
pip install -r requirements.txt
python tasks.py admin
python tasks.py serve 8000
```

Or simply:

```bash
./run.sh
```

This is the intended path for a clean start on a local machine.

---

## 18. Summary

NETRA is a decision-support workstation for fraud, AML, and cybercrime investigation teams operating in data-constrained or offline environments. It combines structured ingestion, graph analysis, explainable risk scoring, and secure local review workflows into a single operational product.

The project is intentionally designed around a core principle: an investigator should be able to trust the system enough to act on the result, because every lead is explainable, every score is tied to evidence, and every output is grounded in a reviewable record.
