#!/usr/bin/env python
"""
NETRA task runner -- cross-platform, standard library only.

WHY THIS FILE EXISTS INSTEAD OF A MAKEFILE
------------------------------------------
`make` is not installed on Windows by default. The team practises on Windows and
ships on Linux, so a Makefile alone would mean one command vocabulary on the
developer's machine and a different one in the container -- and "it worked on my
laptop" is exactly the class of failure this project keeps trying to remove.

This gives ONE vocabulary that behaves identically on both, with nothing to
install. `make` is kept as a thin wrapper over these same commands for Linux,
Docker and CI, so there is still a single implementation.

Usage:
    python tasks.py <command>

Commands:
    gen        Generate the synthetic dataset (with hidden ground truth)
    train      Train the models, measure them, and write artifacts to models/
    replay     Run the windowed pipeline over the dataset and record history
    payload    Build a contract payload for one batch (or all of them)
    drift      Compare each batch against the training distribution
    admin      Create the first administrator (an air-gapped host has no directory)
    retrain    Train a challenger from the analysts' dispositions (never auto-promoted)
    accept     Read any capture file and report exactly what was read and why
    serve      Start the workstation: API, pages and reports
    smoke      Fast end-to-end check: generate -> replay -> contract -> API
    offline    Prove the offline claim: no external reference in any shipped file
    test       Run the pytest suite
    clean      Delete generated data, models, output and history
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
PY = sys.executable

# A small dataset for the smoke test: big enough to exercise every stage, small
# enough that the whole check finishes while you are still looking at it. A smoke
# test you avoid running because it is slow is not a smoke test.
SMOKE_TX = "3000"
SMOKE_CLUSTERS = "120"
SMOKE_DIR = "out/smoke"

# The smoke run keeps its own data, models and state so it can never touch the
# demonstration's history -- the one thing a fast check must not be able to do.
SMOKE_ENV = {
    "NETRA_DATA_DIR": f"{SMOKE_DIR}/data",
    "NETRA_MODELS_DIR": f"{SMOKE_DIR}/models",
    "NETRA_STATE_DIR": f"{SMOKE_DIR}/state",
    "NETRA_UPLOAD_DIR": f"{SMOKE_DIR}/uploads",
}


def _run(title: str, args: list, env: dict | None = None, quiet: bool = False) -> int:
    """Run a subprocess from the project root, echoing what we are doing."""
    if not quiet:
        shown = " ".join(str(a) for a in args)
        # A `-c` snippet is multi-line source, and printing it buries the output
        # that matters underneath the code that produced it.
        if len(args) > 2 and args[1] == "-c":
            shown = f"{args[0]} -c <{len(args[2].splitlines())} lines>"
        print(f"\n=== {title} ===")
        print(f"$ {shown}\n")
    full_env = {**os.environ, "PYTHONPATH": str(ROOT), **(env or {})}
    result = subprocess.run([str(a) for a in args], cwd=ROOT, env=full_env)
    return result.returncode


def cmd_gen(args: list) -> int:
    return _run("Generating the synthetic dataset",
                [PY, "-m", "netra.data.generate", *args])


def cmd_train(args: list) -> int:
    return _run("Training and measuring the models",
                [PY, "-m", "netra.models.train", *args])


def cmd_replay(args: list) -> int:
    """The windowed pipeline: split the capture into batches and record history."""
    return _run("Running the windowed pipeline",
                [PY, "-m", "netra.operations.pipeline", *args])


def cmd_payload(args: list) -> int:
    return _run("Building a contract payload",
                [PY, "-m", "netra.operations.payload", *args])


def cmd_drift(args: list) -> int:
    return _run("Checking each batch against the training distribution",
                [PY, "-m", "netra.models.drift", *args])


def cmd_retrain(args: list) -> int:
    """Train a challenger from what the analysts decided.

    Dispositions are the labels this tool can generate for itself: an analyst who
    closes a lead as confirmed or as a false positive has labelled that wallet. The
    result is registered as a CHALLENGER and never promoted automatically -- a model
    that promotes itself is a model nobody reviewed.
    """
    return _run("Training a challenger from analyst decisions",
                [PY, "-m", "netra.models.feedback", *args])


def cmd_accept(args: list) -> int:
    """Read any capture file the way the workstation will read it.

    This is the acceptance path: point it at a real capture -- however its headers
    are spelled, whatever units its amounts are in -- and it prints what was
    mapped, what was set aside and why, before anything is scored.
    """
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        print("Usage: python tasks.py accept <capture file> [--analyse]")
        return 0
    return _run("Reading a capture the way the workstation reads it",
                [PY, "tools/acceptance.py", *args])


def cmd_admin(args: list) -> int:
    """Create the first administrator.

    An air-gapped host has no email, no directory service and no vendor to call, so
    the first account is created locally, once, by whoever installs it -- and the
    password is typed here rather than shipped in the repository.

        python tasks.py admin              ask for the password, twice, unspoken
        python tasks.py admin --seed       take it from NETRA_ADMIN_PASSWORD
    """
    seeded = "--seed" in args
    print("\n=== Creating the first administrator ===")

    if seeded:
        # Build-time path: a container has no terminal to type into, so the password
        # comes from the environment. It is checked rather than trusted, because a
        # default credential that silently ships is worse than no account at all.
        import os

        name = os.environ.get("NETRA_ADMIN_NAME", "admin")
        display = os.environ.get("NETRA_ADMIN_DISPLAY", "Administrator")
        password = os.environ.get("NETRA_ADMIN_PASSWORD", "")
        if len(password) < 12 or password == "change-this-before-deploying":
            print("\n  NETRA_ADMIN_PASSWORD is missing, too short, or still the"
                  "\n  build-time placeholder. Refusing to create an account that"
                  "\n  anybody who read the Dockerfile would already know.\n")
            return 1
        print(f"  creating '{name}' from NETRA_ADMIN_PASSWORD")
    else:
        name = args[0] if args else input("  username [admin]: ").strip() or "admin"
        display = input("  display name [Administrator]: ").strip() or "Administrator"
        password = _ask_password()
        if not password:
            print("\n  no password given -- nothing was created.\n")
            return 1

    # The password travels in the child's ENVIRONMENT, never in its argv: an
    # argument is visible in the process list to every other user on the host, and
    # this project's own runner echoes the command line it is about to execute.
    return _run("Creating the first administrator",
                [PY, "-c", _ADMIN_SNIPPET],
                env={"NETRA_NEW_ADMIN_NAME": name,
                     "NETRA_NEW_ADMIN_DISPLAY": display,
                     "NETRA_NEW_ADMIN_PASSWORD": password,
                     # So the helper cannot print the credential by accident.
                     "PYTHONWARNINGS": "ignore"})


_ADMIN_SNIPPET = """
import os
from netra.api.auth import open_product_store
from netra.state import product

name = os.environ["NETRA_NEW_ADMIN_NAME"]
display = os.environ["NETRA_NEW_ADMIN_DISPLAY"]
password = os.environ["NETRA_NEW_ADMIN_PASSWORD"]
with open_product_store() as store:
    user = product.bootstrap_admin(store, password, name=name, display_name=display)
print(f"  created {user.name} ({user.role})")
print("  sign in at /sign-in.html")
"""


def _ask_password() -> str:
    """Read a password twice without echoing it.

    `getpass` is standard library on every platform Python ships for, so this
    costs no dependency; the fallback exists because a Windows console without a
    tty would otherwise abort the install.
    """
    try:
        import getpass

        first = getpass.getpass("  password: ")
        again = getpass.getpass("  password again: ")
    except Exception:                                       # pragma: no cover
        first = input("  password (visible): ")
        again = first
    if first != again:
        print("\n  the two passwords differ -- nothing was created.\n")
        return ""
    if len(first) < 8:
        print("\n  use at least 8 characters -- nothing was created.\n")
        return ""
    return first


def cmd_serve(args: list) -> int:
    """Start the workstation. Blocks until interrupted."""
    port = args[0] if args and args[0].isdigit() else "8000"
    extra = [a for a in args if a != port]
    print(f"""
  NETRA is starting on http://localhost:{port}
    Sign in     http://localhost:{port}/sign-in.html
    Workstation http://localhost:{port}/app.html
    Reports     http://localhost:{port}/print/capture   (A4, print to PDF)
    API console http://localhost:{port}/docs

  First time on this machine:  python tasks.py admin
  Offline check: unplug the network now. Everything above keeps working.
""")
    return _run("Starting the workstation",
                [PY, "-m", "uvicorn", "netra.api.app:app", "--host", "127.0.0.1",
                 "--port", port, *extra])


def cmd_test(args: list) -> int:
    return _run("Running the test suite", [PY, "-m", "pytest", "-q", *args])


def cmd_smoke(args: list) -> int:
    """The check that must always pass. If this is red, the project is broken.

    Runs the same sequence a clean checkout would: generate a small dataset, push
    it through the whole windowed pipeline into a throwaway store, build and
    contract-validate a payload, then exercise every HTTP endpoint with the
    authentication rules that protect it.
    """
    steps = [
        ("Smoke 1/5: generate a small dataset",
         [PY, "-m", "netra.data.generate", "--tx", SMOKE_TX,
          "--clusters", SMOKE_CLUSTERS, "--out", f"{SMOKE_DIR}/data"], SMOKE_ENV),
        ("Smoke 2/5: train on it",
         [PY, "-m", "netra.models.train"], SMOKE_ENV),
        ("Smoke 3/5: run the windowed pipeline",
         [PY, "-m", "netra.operations.pipeline",
          "--dataset", f"{SMOKE_DIR}/data/transactions.csv",
          "--store", f"{SMOKE_DIR}/state/netra.sqlite"], SMOKE_ENV),
        ("Smoke 4/5: build and contract-validate a payload",
         [PY, "-m", "netra.operations.payload",
          "--out", f"{SMOKE_DIR}/payload.json"], SMOKE_ENV),
        ("Smoke 5/5: exercise every HTTP endpoint",
         [PY, "tests/api_smoke.py"], SMOKE_ENV),
    ]
    for title, args, env in steps:
        if _run(title, args, env=env) != 0:
            print("\nSMOKE FAILED -- stop and fix this before anything else.\n")
            return 1

    print("\nSMOKE PASSED -- a clean checkout runs end to end and honours the contract.\n")
    return 0


def cmd_offline(args: list) -> int:
    """Prove the offline claim, rather than asserting it.

    The requirement is that the tool makes no outbound request: no CDN, no font
    service, no telemetry, nothing that turns a network cable into a dependency.
    That is a property of the shipped files, so it is checked by reading them:

      * every resource referenced by a page must resolve inside web/
      * no page or stylesheet may contain an absolute http(s) URL
      * the libraries the interface needs must be vendored, not linked

    A tool that quietly fetches a font from the internet fails the one deployment
    the problem statement names, and it fails it in front of the judges with no
    network to fetch from.
    """
    import re

    web = ROOT / "web"
    if not web.exists():
        print(f"\n  no web/ directory at {web}\n")
        return 1

    # Absolute URLs. Our OWN files must not contain one at all: there is no
    # legitimate reason for a page we wrote to name a host. The vendored libraries
    # are different -- their licence headers link to the projects' repositories, and
    # flagging those would teach everyone to ignore this check -- so for vendor
    # files only a URL that a browser would FETCH counts, which means one in a
    # fetch-shaped context.
    external = re.compile(r"https?://(?!127\.0\.0\.1|localhost)[^\s\"')<>]+")
    fetching = re.compile(
        r"(?:fetch\s*\(|\.open\s*\(|importScripts\s*\(|document\.write\s*\(|"
        r"\bsrc\s*=\s*|\bhref\s*=\s*|url\s*\(\s*|@import\s+|import\s*\()"
        r"[\"'(]?\s*(https?://(?!127\.0\.0\.1|localhost)[^\s\"')<>]+)")
    asset_ref = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']')

    problems: list[str] = []
    checked = 0
    vendored = 0
    for path in sorted(web.rglob("*")):
        if path.suffix.lower() not in (".html", ".css", ".js"):
            continue
        checked += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        is_vendor = "vendor" in path.parts
        vendored += int(is_vendor)

        if is_vendor:
            for match in fetching.finditer(text):
                line = text[:match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT)}:{line} would fetch "
                                f"{match.group(1)}")
        else:
            for match in external.finditer(text):
                line = text[:match.start()].count("\n") + 1
                problems.append(f"{path.relative_to(ROOT)}:{line} references "
                                f"{match.group(0)}")

        if path.suffix.lower() == ".html":
            for ref in asset_ref.findall(text):
                if ref.startswith(("http://", "https://", "//", "data:")):
                    problems.append(f"{path.relative_to(ROOT)} loads {ref}")
                    continue
                target = (path.parent / ref.split("?")[0]).resolve()
                if not target.exists():
                    problems.append(f"{path.relative_to(ROOT)} loads {ref}, which is "
                                    "not in the shipped files")

    # The libraries the interface needs must be on disk: a page that falls back to a
    # CDN is a page that does not work on the host this is built for.
    for library in ("web/vendor/vis-network.min.js", "web/vendor/chart.umd.min.js",
                    "web/vendor/fonts.css"):
        if not (ROOT / library).exists():
            problems.append(f"{library} is missing: the interface would have to fetch it")

    print(f"\n=== Offline check: {checked} shipped files "
          f"({vendored} vendored) ===")
    if problems:
        print(f"  {len(problems)} problem(s):")
        for problem in problems[:20]:
            print(f"    - {problem}")
        print("\n  OFFLINE CHECK FAILED -- a network cable is load-bearing.\n")
        return 1
    print("  no external references, no missing assets, libraries vendored.")
    print("  Unplug the network: everything above keeps working.\n")
    return 0


def cmd_clean(args: list) -> int:
    """Delete everything generated. The generator and the code are the source."""
    print("\n=== Cleaning generated artifacts ===")
    keep = {".gitkeep", "README.md"}
    for target in ("data", "models", "out", "uploads"):
        path = ROOT / target
        if not path.exists():
            continue
        for child in path.iterdir():
            if child.name in keep:
                continue      # never delete the sentinels that keep the folder in git
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        print(f"  cleaned {target}/")
    print("\n  The dataset and the models are regenerated by 'python tasks.py gen'"
          "\n  and 'python tasks.py train'. Nothing here was source code.\n")
    return 0


COMMANDS = {
    "gen": cmd_gen,
    "train": cmd_train,
    "replay": cmd_replay,
    "payload": cmd_payload,
    "drift": cmd_drift,
    "accept": cmd_accept,
    "retrain": cmd_retrain,
    "admin": cmd_admin,
    "serve": cmd_serve,
    "run": cmd_serve,          # alias: people reach for "run" first
    "smoke": cmd_smoke,
    "offline": cmd_offline,
    "test": cmd_test,
    "clean": cmd_clean,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        print("Available commands:", ", ".join(sorted(set(COMMANDS) - {"run"})))
        return 0
    name = sys.argv[1]
    handler = COMMANDS.get(name)
    if handler is None:
        print(f"Unknown command: {name}\n")
        print(__doc__)
        return 2
    return handler(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
