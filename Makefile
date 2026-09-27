COMPOSE := docker-compose
PG      := neuralwire-pg

.DEFAULT_GOAL := help
.PHONY: help db db-stop db-reset db-shell db-status api worker frontend

help:  ## List available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

db:  ## Start local Postgres and wait until it accepts connections
	@$(COMPOSE) up -d
	@printf 'postgres: '
	@for i in $$(seq 1 60); do \
		if [ "$$(docker inspect -f '{{.State.Health.Status}}' $(PG) 2>/dev/null)" = healthy ]; then \
			echo 'ready on localhost:5433'; exit 0; \
		fi; \
		printf '.'; sleep 1; \
	done; \
	echo ' timed out'; $(COMPOSE) logs --tail=20 db; exit 1

db-stop:  ## Stop the container, keeping the data
	$(COMPOSE) down

db-reset:  ## Wipe the data and re-seed from the dump
	$(COMPOSE) down -v
	@$(MAKE) --no-print-directory db

db-shell:  ## Open psql on the local database
	docker exec -it $(PG) psql -U stocknews -d stocknews

db-status:  ## Print which database the code resolves to
	@cd python_backend && python3 -c "import db_connection"

api: db  ## Run the FastAPI backend
	python3 -m uvicorn api:app --reload --port 8000

worker: db  ## Run the analysis loop
	python3 run_analysis.py

frontend:  ## Run the Next.js dev server
	cd frontend && npm run dev
