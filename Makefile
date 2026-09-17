.PHONY: help up down logs ingest reingest rag-eval stats sources versions clean \
        vector-db-backup vector-db-restore
.DEFAULT_GOAL := help

POSTGRES_USER ?= ncs
POSTGRES_DB   ?= ncs
OLLAMA_LOCAL_URL ?= http://127.0.0.1:11434
DC = podman-compose --env-file .env

# Cloud deploy target: gcp (GKE) or ocp (OpenShift). Selects which driver and
# backups/ dir the vector-db-* targets use.
TARGET ?= gcp
VECTOR_DB_BACKUP ?= infra/$(TARGET)/backups/ncs-vector.sql.gz

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n",$$1,$$2}'

ollama-check: ## Verify host Ollama is reachable before starting
	@printf 'Checking host Ollama at %s... ' "$(OLLAMA_LOCAL_URL)"
	@if curl --noproxy '*' --fail --silent --connect-timeout 3 --max-time 10 \
		"$(OLLAMA_LOCAL_URL)/api/version" >/dev/null; then echo "ready"; else \
		echo "unreachable"; \
		echo "  systemctl --user set-environment OLLAMA_HOST=0.0.0.0:11434"; \
		echo "  systemctl --user restart ollama"; exit 1; fi

up: ollama-check ## Build + start db + ui (UI at http://localhost:8501)
	$(DC) up -d --build db ui

down: ## Stop the stack (keeps the data volume)
	$(DC) down

logs: ## Tail service logs
	$(DC) logs -f

ingest: ollama-check ## Ingest ALL versions found under data/NCS-* (incremental)
	$(DC) up -d db
	$(DC) build ingest
	$(DC) run --rm ingest

reingest: ollama-check ## Force full rebuild: clear the corpus and re-embed everything
	$(DC) up -d db
	$(DC) build ingest
	$(DC) run --rm -e INGEST_RESET=1 ingest

rag-eval: ## Evaluate dense, word/FTS, hybrid RRF, and diagnostic MMR recall
	$(DC) exec -T ui python eval_rag.py

stats: ## Corpus totals (chunks by version + source_type)
	@$(DC) exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -c \
	  "SELECT metadata->>'version' AS version, metadata->>'source_type' AS type, \
	          count(*) AS chunks FROM doc_chunk GROUP BY 1,2 ORDER BY 1,2;"

sources: ## Show the ingest ledger (what has been loaded)
	@$(DC) exec -T db psql -U $(POSTGRES_USER) -d $(POSTGRES_DB) -c \
	  "SELECT kind, source, chunks, to_char(ingested_at,'YYYY-MM-DD HH24:MI') AS ingested \
	   FROM ingested_source ORDER BY source;"

versions: ## Show version dirs present on disk
	@ls -d data/NCS-* 2>/dev/null | sed 's#data/NCS-#  #; s#-#.#' || echo "  (none)"

clean: ## Stop the stack AND delete the data volume (full reset)
	$(DC) down -v

vector-db-backup: ## Dump local pgvector DB to infra/$(TARGET)/backups (TARGET=gcp|ocp)
	python3 infra/$(TARGET)/db_transfer.py backup --output $(VECTOR_DB_BACKUP)

vector-db-restore: ## Restore a dump into the $(TARGET) cluster Postgres (TARGET=gcp|ocp)
	python3 infra/$(TARGET)/db_transfer.py restore --input $(VECTOR_DB_BACKUP)
