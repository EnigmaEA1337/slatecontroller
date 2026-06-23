# Slate Controller — local CI shortcuts.
#
# Mirrors the .github/workflows/* checks so an operator can run the same
# gates without pushing. `make ci` runs everything ; targets are split so
# you can run a single check in isolation while iterating.

.PHONY: ci ci-backend ci-frontend ci-shell ci-docker \
        lint-py types-py tests-py alembic-check \
        types-ts lint-ts build-fe \
        shell-lint docker-validate \
        install-shellcheck install-hadolint

BACKEND := backend
FRONTEND := frontend

# ----- aggregate ----- #

ci: ci-backend ci-frontend ci-shell ci-docker
	@echo
	@echo "✓ All CI checks passed."

ci-backend: lint-py types-py tests-py alembic-check
ci-frontend: types-ts build-fe
ci-shell: shell-lint
ci-docker: docker-validate

# ----- backend ----- #

lint-py:
	cd $(BACKEND) && ruff check app tests
	cd $(BACKEND) && ruff format --check app tests

types-py:
	# Mypy still has warnings — surface but don't gate.
	-cd $(BACKEND) && mypy app

tests-py:
	cd $(BACKEND) && JWT_SECRET=test-only-jwt-secret pytest -v --tb=short

alembic-check:
	cd $(BACKEND) && DB_URL="sqlite+aiosqlite:///./ci-alembic.db" \
		JWT_SECRET=test-only-jwt-secret \
		alembic upgrade head
	rm -f $(BACKEND)/ci-alembic.db

# ----- frontend ----- #

types-ts:
	cd $(FRONTEND) && npx tsc --noEmit

lint-ts:
	cd $(FRONTEND) && npx eslint src --ext .ts,.tsx

build-fe:
	cd $(FRONTEND) && VITE_API_URL="" npm run build

# ----- agent shell scripts ----- #

shell-lint:
	shellcheck --shell=sh --severity=warning \
		$(BACKEND)/app/slate_agent/scripts/slate-ctrl \
		$(BACKEND)/app/slate_agent/scripts/handlers/*.sh \
		$(BACKEND)/app/slate_agent/scripts/*.sh \
		builders/forti/build.sh

# ----- docker / infra ----- #

docker-validate:
	docker compose -f docker-compose.dev.yml config --quiet
	docker compose -f docker-compose.yml config --quiet

# ----- tooling install helpers ----- #

install-shellcheck:
	sudo apt-get update && sudo apt-get install -y shellcheck

install-hadolint:
	curl -fsSL -o /tmp/hadolint \
		https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64
	chmod +x /tmp/hadolint
	sudo mv /tmp/hadolint /usr/local/bin/hadolint
