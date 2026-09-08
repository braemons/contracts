.PHONY: help artifacts test accept lint clean

# Local checkouts, for the developer path only. `make test` uses none of them.
VSTIMD        ?= ../../vstimd
STATEMACHINED ?= ../../statemachined
TRIALD        ?= ../../triald

IMAGE ?= braemons-rig-tests

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ── What an operator does ─────────────────────────────────────────────────────

artifacts: ## fetch the pinned release packages into artifacts/
	./fetch_artifacts.py

test: artifacts ## install the packages in a container, configure, run
	docker build -f container/Dockerfile -t $(IMAGE) .
	docker run --rm \
		-e STATEMACHINED_DEVICE_TARGET=$(or $(DEVICE),socket://127.0.0.1:5300) \
		$(IMAGE) $(ARGS)

# ── What a developer does ─────────────────────────────────────────────────────
#
# Against local checkouts, building what needs building. Faster, and it answers
# a different question: whether the branches work together, not whether the
# release does. `make test` is the one that gates.
test-local: ## against local checkouts (VSTIMD, STATEMACHINED, TRIALD)
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

# ── What an acceptance run does ───────────────────────────────────────────────
#
# On the rig itself, not in a container: a display, a serial port and wires
# cannot be virtualised, and the point is the installed packages on the actual
# box. The daemons are already running there as services, so this attaches to
# them exactly as the container attaches to its own — a rig is a container with
# wires, and the fixtures do not know the difference.
accept: ## the acceptance suite on a wired rig (see WIRING.md)
	uv sync --quiet --group dev
	uv run --group dev pytest -v --hardware \
		--display $(or $(DISPLAY_ADDR),tcp://127.0.0.1:5555) \
		--executor $(or $(EXECUTOR),http://127.0.0.1:8081) \
		$(ARGS)

lint:
	uv run --group dev ruff check .

clean:
	rm -rf .venv artifacts .pytest_cache .ruff_cache
	-docker rmi $(IMAGE)
