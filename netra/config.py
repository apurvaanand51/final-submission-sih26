"""
One place for every path, version string and deployment-level constant.

WHY A MODULE RATHER THAN LITERALS
---------------------------------
The same value is read by the pipeline, the API, the reports and the tests, and
they must agree. When `engine_version` was a literal in three files, a report
could state a version the engine that produced it had never heard of. Anything
that more than one layer needs lives here, and nowhere else.

WHY THE PATHS ARE ENVIRONMENT-OVERRIDABLE
-----------------------------------------
Three deployments have to run from one codebase: the demonstration on a laptop,
the test suite (which must never touch the demonstration's state) and the
installed service on an air-gapped host where `/srv` is the writable volume. A
hard-coded path makes the second impossible and the third painful. Each variable
has a sane default so nothing must be configured to get started.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# Identity of this build.
#
# `SCHEMA_VERSION` is the payload contract's version and it is what a report
# prints: an output that does not say which contract shaped it cannot be compared
# with one produced after the contract moved.
# --------------------------------------------------------------------------
ENGINE_NAME = "netra"
ENGINE_VERSION = "3.0"
SCHEMA_VERSION = "3.0"

# --------------------------------------------------------------------------
# Where things live.
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent


def _dir(variable: str, default: Path) -> Path:
    return Path(os.environ.get(variable, default))


DATA_DIR = _dir("NETRA_DATA_DIR", ROOT / "data")
MODELS_DIR = _dir("NETRA_MODELS_DIR", ROOT / "models")
STATE_DIR = _dir("NETRA_STATE_DIR", ROOT / "out")
UPLOAD_DIR = _dir("NETRA_UPLOAD_DIR", ROOT / "uploads")
WEB_DIR = _dir("NETRA_WEB_DIR", ROOT / "web")
SCHEMA_PATH = _dir("NETRA_SCHEMA_DIR", ROOT / "schemas") / "netra.schema.json"

STATE_DB = STATE_DIR / "netra.sqlite"

# --------------------------------------------------------------------------
# Review policy.
#
# The floor is configurable at run time (Administration → review floor); these are
# the values a fresh installation starts with, kept here so the default is a
# decision that can be found rather than a number buried in a comparison.
# --------------------------------------------------------------------------
DEFAULT_REVIEW_FLOOR = 50
BAND_THRESHOLDS = ((85, "critical"), (70, "high"), (50, "medium"), (0, "low"))

# --------------------------------------------------------------------------
# Session policy. Idle and absolute lifetimes are separate because they protect
# against different things: an unattended terminal, and a session that is simply
# too old to trust.
# --------------------------------------------------------------------------
SESSION_IDLE_MINUTES = 30
SESSION_ABSOLUTE_HOURS = 12
LOGIN_FAILURE_LIMIT = 5
LOGIN_LOCK_MINUTES = 15
# How long an administrator-issued reset token stays valid. Short, because
# it is handed over by voice or on paper and is single-use.
RESET_TOKEN_MINUTES = 30

# --------------------------------------------------------------------------
# Retention. Defaults are deliberately finite: keeping every score and address
# forever is both a legal exposure and a security one.
# --------------------------------------------------------------------------
RETENTION_SCORE_DAYS = 365
RETENTION_EVENT_DAYS = 730
RETENTION_AUDIT_DAYS = 3650        # the audit trail outlives everything it describes

# --------------------------------------------------------------------------
# Modelling. Values a reviewer will ask about, so they are named.
# --------------------------------------------------------------------------
RANDOM_SEED = 42
CV_FOLDS = 5
# Groups at or above this score get an attribution and a fund trail. Below it, an
# explanation would be describing a score nobody acts on.
EXPLAIN_FLOOR = 50
# A guard against a pathological capture, not a display choice.
LEAD_CAP = 2000


def band_for(score: int) -> str:
    """The band a score falls in. One implementation, so the colour on a chip and
    the threshold the API filters on cannot disagree."""
    for threshold, name in BAND_THRESHOLDS:
        if score >= threshold:
            return name
    return "low"


def ensure_directories() -> None:
    """Create the writable directories if they are missing.

    Called at start-up rather than assumed: a fresh clone has none of them, and a
    missing directory should not be the reason a first run fails with an opaque
    sqlite error.
    """
    for directory in (DATA_DIR, MODELS_DIR, STATE_DIR, UPLOAD_DIR):
        directory.mkdir(parents=True, exist_ok=True)
