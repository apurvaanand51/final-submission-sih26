"""
Shared fixtures. Every test that touches state builds its own throwaway one.

WHY THE TESTS NEVER USE THE DEMONSTRATION'S DIRECTORIES
------------------------------------------------------
The suite writes captures, models and a database. If it wrote them where the
demonstration keeps its history, running the tests would change what the next
`tasks.py serve` shows, and a "passing" suite would be one that quietly deleted the
evidence it was supposed to protect. So every test points `netra.config` at a
temporary tree built for it, and the environment variables are restored afterwards.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netra import config                                       # noqa: E402


def _point_config_at(root: Path, models: Path | None = None,
                     state: Path | None = None, uploads: Path | None = None
                     ) -> dict[str, str | None]:
    """Redirect every path config reads, and return what to restore."""
    previous = {
        "NETRA_DATA_DIR": os.environ.get("NETRA_DATA_DIR"),
        "NETRA_MODELS_DIR": os.environ.get("NETRA_MODELS_DIR"),
        "NETRA_STATE_DIR": os.environ.get("NETRA_STATE_DIR"),
        "NETRA_UPLOAD_DIR": os.environ.get("NETRA_UPLOAD_DIR"),
    }
    data_dir = root if models is not None else root / "data"
    models_dir = models if models is not None else root / "models"
    state_dir = state if state is not None else root / "state"
    uploads_dir = uploads if uploads is not None else root / "uploads"

    os.environ["NETRA_DATA_DIR"] = str(data_dir)
    os.environ["NETRA_MODELS_DIR"] = str(models_dir)
    os.environ["NETRA_STATE_DIR"] = str(state_dir)
    os.environ["NETRA_UPLOAD_DIR"] = str(uploads_dir)
    config.DATA_DIR = data_dir
    config.MODELS_DIR = models_dir
    config.STATE_DIR = state_dir
    config.UPLOAD_DIR = uploads_dir
    config.STATE_DB = config.STATE_DIR / "netra.sqlite"
    config.ensure_directories()
    return previous


def _restore(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    config.DATA_DIR = Path(os.environ.get("NETRA_DATA_DIR", ROOT / "data"))
    config.MODELS_DIR = Path(os.environ.get("NETRA_MODELS_DIR", ROOT / "models"))
    config.STATE_DIR = Path(os.environ.get("NETRA_STATE_DIR", ROOT / "out"))
    config.UPLOAD_DIR = Path(os.environ.get("NETRA_UPLOAD_DIR", ROOT / "uploads"))
    config.STATE_DB = config.STATE_DIR / "netra.sqlite"


@pytest.fixture
def sandbox(tmp_path):
    """An isolated set of directories, restored when the test ends."""
    previous = _point_config_at(tmp_path)
    try:
        yield tmp_path
    finally:
        _restore(previous)


@pytest.fixture
def workspace(trained_workspace, tmp_path):
    """Config pointed at the SHARED trained workspace, with private state.

    Two fixtures answer two different questions. `sandbox` gives a test somewhere
    private to write. `workspace` gives it the capture, the models and the replayed
    store the session built once -- because a test that needs a trained model must
    not be reading an empty directory.

    The STATE is copied into the test's own directory and config points there.
    Without that, one test recording analyst decisions or replaying a capture would
    change the store every later test in the session reads, and the suite would pass
    or fail depending on its own order. Data and models are shared read-only: they
    are 8 MB of artifacts that nothing here writes.
    """
    import shutil

    root = trained_workspace
    private_state = tmp_path / "state"
    private_state.mkdir(parents=True, exist_ok=True)
    for item in (root / "state").iterdir():
        if item.is_file():
            shutil.copy2(item, private_state / item.name)

    previous = _point_config_at(root / "data", root / "models", private_state, tmp_path)
    try:
        yield root
    finally:
        _restore(previous)


@pytest.fixture(scope="session")
def trained_workspace(tmp_path_factory):
    """A small capture, trained models and a replayed store, built once.

    Session-scoped because training and replaying are the slow part of this suite:
    the tests that need a real analysis share one, and the ones that do not never
    pay for it. The data is deliberately small -- enough for every stage to run,
    not enough to be slow -- and every assertion about it is about structure and
    reconciliation rather than about the particular numbers, which belong to the
    measured report rather than to a unit test.
    """
    root = tmp_path_factory.mktemp("workspace")
    previous = _point_config_at(root)
    try:
        from netra.data.generate import Generator, write_ground_truth, write_transactions
        from netra.models.train import train
        from netra.operations.pipeline import replay

        data_dir = root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        dataset = data_dir / "transactions.csv"
        generator = Generator(seed=7, n_clusters=90, n_background=2500)
        generator.build()
        write_transactions(generator, dataset)
        write_ground_truth(generator, data_dir)
        assert dataset.exists(), "the generator did not write a capture"
        train(data_dir=data_dir, models_dir=root / "models", seed=7)
        assert (root / "models" / "risk.joblib").exists(), "training wrote no model"
        replay(store_path=root / "state" / "netra.sqlite", dataset=dataset,
               data_dir=data_dir, models_dir=root / "models", reset=True)
        yield root
    finally:
        _restore(previous)


@pytest.fixture
def analysis_store(trained_workspace):
    """The store the shared workspace was replayed into."""
    from netra.state.store import MonitoringStore

    with MonitoringStore(trained_workspace / "state" / "netra.sqlite") as store:
        yield store
