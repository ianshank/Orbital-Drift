# Orbital-Drift — task-runner front-end (adopt-governance-kit, design D1/D2/D12).
#
# THIN BY DESIGN. ci/checks.sh is the single source of truth for every gate:
# each gate target below is exactly `sh ci/checks.sh <stage>` and nothing else,
# so `make <gate>` and CI cannot diverge — tests/governance/test_governance_meta.py
# asserts every gate target maps to a checks.sh dispatch label and that no
# recipe reconstructs a gate inline. A gate target lands here only in the same
# PR as its checks.sh stage (no dangling targets).
#
# On a box without GNU make, call `sh ci/checks.sh <stage>` directly — CI
# (Linux) is authoritative (design D12). The only non-delegating recipes are
# `install`, `format`, `guard-probe` and `clean`, none of which is a gate.

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

PYTHON ?= python

.PHONY: help install test lint format types dead secrets specs audit \
        coverage traceability projections governance guard-probe contract smoke hooks pre-pr clean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Bootstrap: the one documented command path (Principle IV) + git hooks
	$(PYTHON) -m pip install -e ".[dev]"
	bash "$(ROOT)/scripts/install_hooks.sh"

# --- gates: every recipe below delegates to ci/checks.sh (design D1) --------

test: ## Unit suite (zero skipped, enforced by the conftest guard)
	sh "$(ROOT)/ci/checks.sh" unit

lint: ## ruff check + format --check
	sh "$(ROOT)/ci/checks.sh" lint

types: ## mypy strict
	sh "$(ROOT)/ci/checks.sh" typecheck

dead: ## vulture dead-code scan
	sh "$(ROOT)/ci/checks.sh" dead

secrets: ## gitleaks working tree + history (pinned container)
	sh "$(ROOT)/ci/checks.sh" gitleaks

specs: ## OpenSpec structural validation (deterministic, design D13)
	sh "$(ROOT)/ci/checks.sh" specs

audit: ## pip-audit dependency vulnerability scan
	sh "$(ROOT)/ci/checks.sh" audit

coverage: ## FR-011a coverage floor (global, ci/versions.env) + per-file floor (covcheck)
	sh "$(ROOT)/ci/checks.sh" coverage

traceability: ## Requirement-traceability matrix lint
	sh "$(ROOT)/ci/checks.sh" traceability

projections: ## Generated-planning drift check (roadmap + CSV byte-match roadmap_data.py)
	sh "$(ROOT)/ci/checks.sh" projections

contract: ## Contract suite (declared-empty until T013+)
	sh "$(ROOT)/ci/checks.sh" contract

smoke: ## DAG smoke suite (declared-empty until T020)
	sh "$(ROOT)/ci/checks.sh" smoke

hooks: ## pre-commit hook enforcement (as CI runs it)
	sh "$(ROOT)/ci/checks.sh" hooks

# --- convenience (non-gate) ----------------------------------------------

format: ## Apply ruff formatting
	$(PYTHON) -m ruff format .

guard-probe: ## Show the PreToolUse guard's verdict for CMD, with the reason
	@test -n "$(CMD)" || { echo 'usage: make guard-probe CMD="<command>"'; exit 2; }
	@bash "$(ROOT)/scripts/guard_probe.sh" "$(CMD)"

serve: ## Run FastAPI canary inference server locally
	$(PYTHON) -m uvicorn orbital_drift.serve.app:app --host 0.0.0.0 --port 8000

docker-build: ## Build multi-stage production container
	docker build -t orbital-drift:latest .

docker-run: ## Run production container locally
	docker run -p 8000:8000 orbital-drift:latest

governance: ## Governance meta-tests (guards, zero-skip, skill freshness)
	sh "$(ROOT)/ci/checks.sh" governance

# --- the pre-PR gate ------------------------------------------------------
# `sh ci/checks.sh all` already chains every stage in CI order (stage_all is
# extended in the same PR as each new stage), so this target is a pure alias —
# the single-harness rule (design D1) kept literal.

pre-pr: ## Everything CI runs, in CI order
	sh "$(ROOT)/ci/checks.sh" all
	@echo
	@echo "pre-PR validation complete — every gate CI runs has passed locally."

clean: ## Remove build/test caches (never touches tracked files)
	rm -rf .pytest_cache .ruff_cache .mypy_cache coverage.xml coverage.json .coverage
	find "$(ROOT)" -type d -name __pycache__ -prune -exec rm -rf {} +
