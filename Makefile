# NETRA -- Linux / CI convenience targets.
#
# These are THIN WRAPPERS over tasks.py, not a second implementation. `make` is
# not installed on Windows by default, so the team's daily vocabulary is
# `python tasks.py <command>`; this file exists so Linux, Docker and CI can use
# the conventional spelling without a divergent code path.
#
#   make gen | train | replay | payload | drift | admin | accept | serve | smoke |
#        offline | test | clean
#
PY ?= python3
PORT ?= 8000
FILE ?= uploads/capture.csv

.PHONY: help gen train replay payload drift admin accept retrain serve smoke offline test clean docker offline-check

help:
	@$(PY) tasks.py --help

gen:
	@$(PY) tasks.py gen

train:
	@$(PY) tasks.py train

replay:
	@$(PY) tasks.py replay

payload:
	@$(PY) tasks.py payload

drift:
	@$(PY) tasks.py drift

admin:
	@$(PY) tasks.py admin

accept:
	@$(PY) tasks.py accept $(FILE)

retrain:
	@$(PY) tasks.py retrain

serve:
	@$(PY) tasks.py serve $(PORT)

smoke:
	@$(PY) tasks.py smoke

offline:
	@$(PY) tasks.py offline

test:
	@$(PY) tasks.py test

clean:
	@$(PY) tasks.py clean

# Build and start the image, then prove the service came up. The Dockerfile bakes
# the dataset and the models in, so `docker compose up` on an air-gapped machine
# produces a populated workstation with no setup step.
#
# The probe is the SIGN-IN PAGE, not an API route: every API route needs a session,
# so a health check on one of them reports a healthy service as unhealthy the
# moment an administrator changes a password.
docker:
	docker compose up --build -d
	@sleep 8
	@curl -fsS http://localhost:$(PORT)/sign-in.html >/dev/null && echo "health: OK" \
		|| (echo "health: FAILED"; exit 1)

# The claim the problem statement actually makes: it runs with no network. This
# first reads every shipped file to prove nothing in the interface reaches out
# (tasks.py offline), then builds from scratch -- so no cached layer can hide a
# download -- and starts it.
offline-check:
	@$(PY) tasks.py offline
	docker compose build --no-cache
	docker compose up -d
	@sleep 8
	@curl -fsS http://localhost:$(PORT)/sign-in.html >/dev/null \
		&& echo "OFFLINE BUILD+START OK" \
		|| (echo "OFFLINE CHECK FAILED"; exit 1)
	docker compose down
