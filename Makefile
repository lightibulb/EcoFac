.PHONY: check-no-legacy
check-no-legacy: ## fail if production code imports legacy decision modules
	@python3 scripts/check_no_legacy_imports.py
