# ═══════════════════════════════════════════════════════════════════════════
# Omni-Auditor SaaS Dashboard — Development Makefile
# Quick commands for managing the Docker Compose development environment.
# ═══════════════════════════════════════════════════════════════════════════

# ─── Configuration ────────────────────────────────────────────────────────
COMPOSE_FILE := docker-compose.yml
PROJECT_NAME := omni-auditor

# ─── Main Targets ─────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help message
	@echo "Omni-Auditor SaaS Dashboard — Development Commands"
	@echo "═══════════════════════════════════════════════════════"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: up
up: ## Start all services in detached mode
	@echo "🚀 Starting Omni-Auditor services..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) up -d
	@echo ""
	@echo "Services starting up:"
	@echo "  Frontend: http://localhost"
	@echo "  Backend:  http://localhost/api/v1"
	@echo "  API Docs: http://localhost/docs"
	@echo "  DB:       localhost:5432"
	@echo "  Redis:    localhost:6379"

.PHONY: down
down: ## Stop and remove all containers
	@echo "🛑 Stopping Omni-Auditor services..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) down

.PHONY: logs
logs: ## Tail logs from all services
	@echo "📋 Tailing logs (Ctrl+C to exit)..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) logs -f

.PHONY: logs-backend
logs-backend: ## Tail logs from backend only
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) logs -f backend

.PHONY: logs-worker
logs-worker: ## Tail logs from celery worker
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) logs -f celery-worker

.PHONY: migrate
migrate: ## Run Alembic database migrations
	@echo "🗃️  Running database migrations..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		alembic upgrade head

.PHONY: migrate-make
migrate-make: ## Create a new Alembic migration (set MSG=migration_name)
	@echo "📝 Creating new migration: $(MSG)..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		alembic revision --autogenerate -m "$(MSG)"

.PHONY: test
test: ## Run backend tests
	@echo "🧪 Running tests..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		pytest -v --cov=app --cov-report=term-missing

.PHONY: test-watch
test-watch: ## Run tests in watch mode
	@echo "👁️  Running tests in watch mode (Ctrl+C to exit)..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		pytest -f -v

.PHONY: lint
lint: ## Run linting on backend code
	@echo "🔍 Running linters..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		ruff check .
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		mypy .

.PHONY: lint-frontend
lint-frontend: ## Run linting on frontend code
	@echo "🔍 Running frontend linters..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec frontend \
		npm run lint

.PHONY: format
format: ## Format backend code
	@echo "✨ Formatting code..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend \
		ruff format .

.PHONY: format-frontend
format-frontend: ## Format frontend code
	@echo "✨ Formatting frontend code..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec frontend \
		npm run format

.PHONY: seed
seed: ## Seed the database with sample data (runs init.sql)
	@echo "🌱 Seeding database..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec db \
		psql -U $(POSTGRES_USER:-postgres) -d $(POSTGRES_DB:-omniauditor) \
		-f /docker-entrypoint-initdb.d/init.sql

.PHONY: psql
psql: ## Open PostgreSQL interactive shell
	@echo "🐘 Opening PostgreSQL shell..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec db \
		psql -U $(POSTGRES_USER:-postgres) -d $(POSTGRES_DB:-omniauditor)

.PHONY: redis-cli
redis-cli: ## Open Redis CLI
	@echo "🔴 Opening Redis CLI..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec redis redis-cli

.PHONY: build
build: ## Rebuild all Docker images
	@echo "🔨 Rebuilding all images..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) build --no-cache

.PHONY: build-backend
build-backend: ## Rebuild backend image only
	@echo "🔨 Rebuilding backend image..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) build --no-cache backend

.PHONY: build-frontend
build-frontend: ## Rebuild frontend image only
	@echo "🔨 Rebuilding frontend image..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) build --no-cache frontend

.PHONY: clean
clean: ## Remove all containers, volumes, and images
	@echo "🧹 Cleaning up all containers, volumes, and networks..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) down -v --rmi all --remove-orphans
	@echo "Cleanup complete."

.PHONY: clean-volumes
clean-volumes: ## Remove only persistent volumes (DELETES ALL DATA)
	@echo "⚠️  Removing all persistent data volumes..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) down -v

.PHONY: restart
restart: ## Restart all services
	@echo "🔄 Restarting all services..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) restart

.PHONY: restart-backend
restart-backend: ## Restart backend only
	@echo "🔄 Restarting backend..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) restart backend

.PHONY: status
status: ## Show running containers
	@echo "📊 Service Status:"
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) ps

.PHONY: health
health: ## Check health of all services
	@echo "🏥 Checking service health..."
	@docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) ps | grep -E "(Name|----|omni-auditor)" || true
	@echo ""
	@echo "Backend health check:"
	@curl -s http://localhost:8000/api/v1/health 2>/dev/null || echo "  Backend not responding on :8000"

.PHONY: shell-backend
shell-backend: ## Open a shell in the backend container
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec backend /bin/sh

.PHONY: shell-db
shell-db: ## Open a shell in the database container
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) exec db /bin/sh

.PHONY: worker-restart
worker-restart: ## Restart celery worker
	@echo "🔄 Restarting Celery worker..."
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) restart celery-worker

.PHONY: worker-logs
worker-logs: ## Show celery worker logs
	docker-compose -f $(COMPOSE_FILE) -p $(PROJECT_NAME) logs -f celery-worker
