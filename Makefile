.PHONY: help test test-integration lint format yaml-lint prettier validate validate-fleet config-show secrets-dry-run install-tools run docker-build docker-run

help:  ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Usage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---- test ----------------------------------------------------------------

test:  ## Run the unit-test suite.
	cd review-surface && ./.venv-test/bin/python -u -c "import main, sys; r = main.run_tests(); sys.exit(0 if r['failed'] == 0 else 1)" 2>&1

test-integration:  ## Run dispatcher integration tests.
	./review-surface/.venv-test/bin/python -u review-surface/test_dispatcher_integration.py

# ---- lint ----------------------------------------------------------------

lint:  ## Run ruff linter on review-surface + tools.
	ruff check review-surface/ tools/review-surface-boot/

format:  ## Apply ruff format.
	ruff format review-surface/ tools/review-surface-boot/

yaml-lint:  ## Lint all YAML files.
	@for f in $(shell find .github -name '*.yml' -o -name '*.yaml'); do python3 -c "import yaml; yaml.safe_load(open('$$f')); print('OK $$f')"; done
	@python3 -c "import yaml; yaml.safe_load(open('lefthook.yml')); print('OK lefthook.yml')"
	@python3 -c "import yaml; print('OK sonar-project.properties')" 2>/dev/null || true

prettier:  ## Run prettier on all YAML files.
	npx --yes prettier --check $(shell find . -path ./.git -prune -o -name '*.yml' -print -o -name '*.md' -print | grep -v node_modules)

# ---- validate ------------------------------------------------------------

validate:  ## Run validate-fleet.sh end-to-end.
	bash tools/review-surface-boot/validate-fleet.sh

validate-fleet:  ## Same as validate (alias).
	bash tools/review-surface-boot/validate-fleet.sh

config-show:  ## Print active review-surface settings (from config.yaml + env).
	cd review-surface && ./.venv-test/bin/python -u -c "import main, json; print(json.dumps({'tool_backends': list(getattr(main, 'AVAILABLE_BACKENDS', [])), 'rate_limit_per_hour': main.settings.rate_limit_per_hour}, indent=2))" 2>&1

# ---- secrets -------------------------------------------------------------

secrets-dry-run:  ## Preview REVIEW_SURFACE_URL/TOKEN wiring across fleet (no writes).
	REVIEW_SURFACE_URL="https://review.example.invalid" \
	REVIEW_SURFACE_TOKEN="preview-token-preview-token-preview-token-preview-token-preview" \
	bash tools/review-surface-boot/set-fleet-secrets.sh --dry-run

# ---- install / run --------------------------------------------------------

install-tools:  ## Print install instructions for required tools.
	@echo "Install these with pipx:"
	@echo "  pipx install lefthook"
	@echo "  pipx install ruff"
	@echo "  pipx install yamllint"
	@echo "  npx --yes prettier --version"

run:  ## Run the review-surface API on :8000.
	cd review-surface && ./.venv-test/bin/python -u -c "import main, uvicorn; uvicorn.run(main.app, host='0.0.0.0', port=8000)" 2>&1

# ---- docker --------------------------------------------------------------

docker-build:  ## Build the review-surface Docker image.
	docker build -t phenotype/review-surface:dev .

docker-run:  ## Run review-surface container.
	docker run --rm -p 8000:8000 \
		-e REVIEW_GITHUB_TOKEN \
		-e REVIEW_SURFACE_TOKEN \
		phenotype/review-surface:dev
