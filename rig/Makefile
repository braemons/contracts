.PHONY: help test test-local lint clean

# Local checkouts, for `make test-local`. Override if yours live elsewhere.
VSTIMD        ?= ../../vstimd
STATEMACHINED ?= ../../statemachined
TRIALD        ?= ../../triald

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

test: ## against the pinned releases — what an operator installs
	@echo "Not yet possible: none of the three publishes what this needs."
	@echo "See README.md, 'The bootstrap gap'. Use 'make test-local' meanwhile."
	@exit 1

# The development path, and scaffolding rather than the design.
#
# The clients are installed from local checkouts *as built*, which for vstimd
# means its generated protobuf stubs must already exist -- `make proto` in
# client/python. They are gitignored, so a source install without them produces
# a package that imports and immediately fails. The published wheel is fine (its
# workflow regenerates them and refuses a wheel that lacks them); it is only the
# from-source path that is broken, which is one more reason the design installs
# releases.
test-local: ## against local checkouts (see VSTIMD, STATEMACHINED, TRIALD)
	uv sync --quiet --group dev
	uv pip install --quiet \
		-e $(TRIALD) \
		-e $(STATEMACHINED)/daemon \
		-e $(VSTIMD)/client/python \
		fastapi uvicorn pydantic httpx
	VSTIMD_BINARY=$(abspath $(VSTIMD))/target/release/vstimd \
	STATEMACHINED_DEVICE=$(abspath $(STATEMACHINED))/build/statemachined_native_device \
	STATEMACHINED_BENCH=$(abspath $(STATEMACHINED))/daemon/bench \
	uv run --group dev --no-sync pytest -v $(ARGS)

lint:
	uv run --group dev ruff check .

clean:
	rm -rf .venv artifacts .pytest_cache
