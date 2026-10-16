.PHONY: help setup up down clean ps logs ingest backfill load transform freshness docs test lint

START ?= 2025-10-01
END   ?= 2026-09-30
AS_OF ?= 2026-10-15

help:  ## list available commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

setup:  ## create .env from the template
	@test -f .env || cp .env.example .env && echo ".env ready"

up:  ## start lake + warehouse
	docker compose up -d --wait

down:  ## stop containers (keeps data)
	docker compose down

clean:  ## stop containers AND delete all data volumes
	docker compose down -v

ps:  ## container status
	docker compose ps

logs:  ## follow infrastructure logs
	docker compose logs -f seaweedfs postgres

ingest:  ## land today's batch in the lake
	docker compose run --rm --build ingestion run

backfill:  ## land one batch per day: make backfill START=... END=... AS_OF=...
	docker compose run --rm --build ingestion backfill --start $(START) --end $(END) --as-of $(AS_OF)

load:  ## load committed lake dates into Postgres raw (incremental)
	docker compose run --rm --build loader run

transform:  ## dbt build: staging -> intermediate -> marts, with tests
	docker compose run --rm --build dbt build

freshness:  ## warn/error when raw has not been loaded recently
	docker compose run --rm --build dbt source freshness

docs:  ## dbt docs (lineage, columns, tests) at http://localhost:8080 - Ctrl+C to stop
	docker compose --profile docs up --build dbt-docs

test:  ## run unit + integration tests locally (loader tests need `make up`)
	cd ingestion && python -m pytest -q
	cd loader && python -m pytest -q

lint:  ## static checks
	cd ingestion && ruff check . && ruff format --check .
	cd loader && ruff check . && ruff format --check .
