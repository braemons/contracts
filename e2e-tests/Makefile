.PHONY: help artifacts test test-local accept accept-docker accept-pi lint clean

# Local checkouts, for the developer path only. `make test` uses none of them.
VSTIMD        ?= ../../vstimd
STATEMACHINED ?= ../../statemachined
TRIALD        ?= ../../triald

IMAGE ?= braemons-e2e-tests

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
		-e "$(STATEMACHINED)/python[serve]" \
		-e $(VSTIMD)/client/python \
		fastapi uvicorn pydantic httpx
	VSTIMD_BINARY=$(abspath $(VSTIMD))/target/release/vstimd \
	STATEMACHINED_NATIVE_DEVICE=$(abspath $(STATEMACHINED))/build/statemachined_native_device \
	uv run --group dev --no-sync pytest -v $(ARGS)

# ── What an acceptance run does ───────────────────────────────────────────────
#
# On the rig itself, not in a container: a display, a serial port and wires
# cannot be virtualised, and the point is the installed packages on the actual
# box. The daemons are already running there as services, so this attaches to
# them exactly as the container attaches to its own — a rig is a container with
# wires, and the fixtures do not know the difference.
#
# The two libraries the tests import come from the same pins as the container's:
# vstimd-client from PyPI, triald's wheel from its release.
accept: artifacts ## the whole suite on a wired rig, with its daemons running (see WIRING.md)
	uv sync --quiet --group dev
	uv pip install --quiet "$$(grep '^vstimd-client' artifacts/requirements.txt)" artifacts/triald-*.whl
	uv run --group dev --no-sync pytest -v --hardware \
		--display $(or $(DISPLAY_ADDR),tcp://127.0.0.1:5555) \
		--executor $(or $(EXECUTOR),http://127.0.0.1:8081) \
		$(ARGS)

# Interactive: both explain what they will do and ask first. See WIRING.md.
accept-docker: ## real board on USB, daemons in Docker; installs nothing here
	./run_on_hardware.py docker $(ARGS)

accept-pi: ## a Raspberry Pi test system with the board attached (runs sudo)
	sudo ./run_on_hardware.py pi $(ARGS)

lint:
	uv run --group dev ruff check .

clean:
	rm -rf .venv artifacts .pytest_cache .ruff_cache
	-docker rmi $(IMAGE)
