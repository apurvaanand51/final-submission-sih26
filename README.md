# NETRA — final submission

**Network-Enhanced Transaction Risk Analysis**
An offline workstation for financial-intelligence and cybercrime teams.
Nothing leaves the machine.

Team Vortex · SIH 2026 · PS SIH26146 · NTRO

---

## What this folder is

The product build. It is **authored from scratch** — the previous prototype is kept
outside this folder and used only as a reference for algorithms, thresholds and the
lessons it paid for. Nothing was copied in.

| Path | What lives there |
|---|---|
| `docs/NETRA_UI_Design.pdf` | **The design authority.** 40 pages, every screen, light and dark, plus the states page. |
| `netra/` | The product package: data → identify → correlate → features → models → state → operations → work → report → api |
| `web/` | The interface: static HTML/CSS/JS, no build step, with the vendored libraries and 42 font files |
| `schemas/netra.schema.json` | **The frozen payload contract**, checked on the payload as served |
| `tests/` | 80 pytest checks, the contract validator, and a 75-check endpoint smoke test |
| `tools/` | The acceptance path (`acceptance.py`) and the foreign-shape harness (`foreign_shape.py`) |
| `data/ models/ out/ uploads/ wheels/` | Generated and runtime state. Nothing here is source. |

## Where the build stands

| Layer | State |
|---|---|
| `netra/config.py` | **done** — every path, version and policy constant, environment-overridable |
| `netra/data/` | **done** — four readers, ~90 column aliases, quality gate, satoshi + timezone detection, all typologies generated |
| `netra/identify/`, `netra/correlate/` | **done** — `NTR-####` / `IP-####` identity, co-spend clustering, CoinJoin exclusion, flow vs control edges |
| `netra/features/` | **done** — 33 features including both campaign detectors (poisoning, pool sweeps) |
| `netra/models/` | **trained from scratch** — calibrated forest, IsolationForest, attribution, drift reference, scorecard measured |
| `netra/state/` | **done** — analysis store + the product tables (users, sessions, cases, dispositions, canaries, model registry, runs) |
| `netra/operations/` | **done** — windowed replay, contract payload, monitoring events, windows and history |
| `netra/work/` | **done** — the service layer over the product tables |
| `netra/api/` | **done** — 44 routes, scrypt credentials, server-side sessions, CSRF, role capabilities, contract validation on serve |
| `web/` | **done** — one shell, 16 screens, the dense operations map, A4 reports |
| `netra/report/` | **done** — capture, leads, case dossier and campaign sheets with engine, model version and evidence hash |
| `tests/` | **done** — contract, ingest, auth, state, and the endpoint smoke test |

## Run it

```bash
python tasks.py admin          # create the first administrator (once per machine)
python tasks.py serve 8000     # or ./run.sh, or run.bat on Windows
```

The administrator's password is typed at that prompt and stored as a scrypt
credential -- it is not in this repository. An administrator issues a single-use
reset token from Administration for anyone who forgets theirs; there is no email
on an air-gapped host, so the token is handed over out of band.

On a fresh checkout, `./run.sh` (or `run.bat`) does everything else by itself:
generate the capture, train the models, replay the windows, then serve. It prepares
only what is missing, so a second run needs no computation and no network.

```bash
python tasks.py gen            # synthetic capture with planted typologies
python tasks.py train          # train and measure the models, write artifacts
python tasks.py replay         # windowed pipeline -> out/netra.sqlite
python tasks.py accept FILE    # read a real capture and report what was made of it
python tasks.py retrain        # train a challenger from the analysts' dispositions
python tasks.py offline        # prove no shipped file references the network
python tasks.py smoke          # generate -> train -> replay -> contract -> every endpoint
python tasks.py test           # the pytest suite
```

## The acceptance path: your dataset, not ours

A demonstration that only works on the dataset it shipped with proves nothing. The
question an examiner actually asks is *"take my file — the one my tool exported —
and show me what you make of it."* So that is a command:

```bash
python tasks.py accept uploads/your_capture.csv            # read and report
python tasks.py accept uploads/your_capture.csv --analyse  # then analyse it
```

It prints what the workstation did to the file, in the order it does it:

1. **READ** — which of your columns became which canonical field, and which were
   left alone
2. **GATE** — how many rows were usable, and a reason for every row set aside
3. **UNITS** — whether amounts arrived in satoshis and were converted, and which
   timezone was assumed when the file did not say
4. **ABSENT** — every field the analysis uses that your file did not contain, and
   what that costs (`no source network endpoint: the wallet groups in this capture
   have no country or hosting operator attributed to them`)
5. **LEADS** — optionally, the pipeline run on your file: batch by batch, then the
   ranked leads with their band, typology, strongest driver and a check that the
   attribution reconciles to the displayed score

The header names do not have to match anything. `Block Time`, `Transaction Hash`,
`Sender`, `Recipient`, `Value` are understood; so are `from_address`, `txhash`,
`inputs`, `amount`, `country` and about eighty more spellings. Amounts in satoshis
are detected and divided by 100,000,000 — with the conversion stated, because a
capture read in the wrong unit describes an economy a hundred million times too
large. Rows that cannot be read are counted with their reason, never dropped in
silence: in an investigation a missing row is a missing fact.

`tools/foreign_shape.py` builds a deliberately awkward file from a known capture
(different headers, satoshis, no timezone, several addresses in one cell, unreadable
rows, no network columns) so the reader can be tested against a file whose contents
are known. `tests/test_ingest.py` is the same idea, faster.

## What is verified, and how

| Claim | How it is checked |
|---|---|
| The payload honours its contract | `schemas/netra.schema.json` + `tests/validate_contract.py`, enforced in `payload_for()` and on `/api/results`; a payload that breaks it raises instead of being served |
| Nothing reaches the network | `python tasks.py offline` reads every shipped file: no external URL in our own code, no fetch-shaped URL in the vendored libraries, every referenced asset present |
| A clean checkout works end to end | `python tasks.py smoke` — generate, train, replay, validate the contract, then 75 checks across every HTTP endpoint |
| Authentication is a control, not a decoration | `tests/test_auth.py` — unauthenticated pages redirect, API paths answer 401, a write without a CSRF header is refused, a role without a capability is refused, repeated failures lock the account, a reset token works once and expires |
| The audit log cannot be rewritten | A trigger in the schema raises on `UPDATE` and `DELETE`; `tests/test_state.py` proves it |
| A replay does not destroy records | The analysis history is cleared table by table; accounts, cases and the audit log survive (`tests/test_state.py`) |
| Identity is stable and content-derived | 4,000 anchors produce 4,000 distinct `NTR-` ids, escalating 4 → 6 → 8 digits on collision; the same capture replayed produces the same ids |
| Analyst decisions can improve the model, and cannot promote it | `python tasks.py retrain` trains a challenger from dispositions (refusing under 8 labels), writes it beside the champion and registers it as a challenger; `tests/test_feedback.py` proves it does not touch the live artifact and that a confirmed outcome outranks a later judgement |
| The model's numbers are measured, not asserted | `netra/models/train.py` writes `metrics.json`; the scorecard screen and report read it, including the honest part — a linear model over the same features reaches a comparable score, so most of the signal is in the feature engineering |

## Two constraints that shape everything

**No network access, ever.** No CDN, no remote fonts, no map tiles, no telemetry,
no package index at install time. Every asset is in this folder. The build is
checked rather than trusted: `python tasks.py offline` fails if any shipped file
references an external host.

**A lead is not a finding of guilt.** Every score ranks a group for review, and the
attribution reconciles exactly to the number displayed beside it. That statement
appears on every screen, every report and every export.

---

*The design PDF is the authority for the interface. `NETRA_PRODUCT_SPEC.md`, one
level up, is the specification this build implements.*
