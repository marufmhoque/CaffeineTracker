PY ?= .venv/bin/python
ifeq ($(OS),Windows_NT)
PY := .venv/Scripts/python.exe
endif

.PHONY: setup dev api web test all digest

setup:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -r backend/requirements.txt
	npm --prefix frontend install
	test -f .env || cp .env.example .env
	$(PY) -m backend.app.cli init

api:
	$(PY) -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

web:
	npm --prefix frontend run dev

dev: setup
	$(MAKE) -j2 api web

test:
	$(PY) -m pytest -q

all:
	$(PY) -m backend.app.cli all

digest:
	$(PY) -m backend.app.cli digest
