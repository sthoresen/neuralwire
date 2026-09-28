.DEFAULT_GOAL := help
.PHONY: help up down reset logs db-shell test lint fmt seed

help:  ## List available targets
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

up:  ## Start db + API + frontend (http://localhost:3000)
	docker compose up --build -d --wait
	@echo "Frontend: http://localhost:3000  API docs: http://localhost:8000/docs"

down:  ## Stop the stack, keeping the local database
	docker compose down

reset:  ## Delete the local database and start fresh from the demo seed
	docker compose down -v
	@$(MAKE) --no-print-directory up

logs:  ## Follow logs from all services
	docker compose logs -f

db-shell:  ## Open psql on the local database
	docker compose exec db psql -U stocknews -d stocknews

test:  ## Run the test suite
	pytest

lint:  ## Lint and check formatting (what CI runs)
	ruff check . && ruff format --check .

fmt:  ## Apply lint fixes and formatting
	ruff check --fix . && ruff format .

seed:  ## Refresh db/init/01-demo-seed.sql from SOURCE_DATABASE_URL (read-only)
	db/pull_demo_seed.sh
