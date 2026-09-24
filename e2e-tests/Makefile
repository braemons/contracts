.PHONY: help artifacts test test-local check-checkouts accept accept-docker accept-pi lint clean

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
# **The paths are checked before anything is installed.** They rotted silently
# once: both daemons moved their package into `daemon/` and this target went on
# naming `$(TRIALD)` and `$(STATEMACHINED)/python`, which uv reports as "does
# not appear to be a Python project" — a message about the wrong thing. CI never
# runs this target, so nothing else can notice.
CHECKOUTS = \
	$(TRIALD)/daemon/pyproject.toml \
	$(STATEMACHINED)/daemon-rs/Cargo.toml \
	$(STATEMACHINED)/client/python/pyproject.toml \
	$(VSTIMD)/client/python/pyproject.toml

check-checkouts:
	@missing=""; for f in $(CHECKOUTS); do [ -f "$$f" ] || missing="$$missing  $$f"; done; \
	if [ -n "$$missing" ]; then \
	  echo "make test-local needs three checkouts beside this one, and cannot find:"; \
	  echo "$$missing" | tr ' ' '\n' | grep -v '^$$' | sed 's/^/  /'; \
	  echo ""; \
	  echo "Point them at yours: make test-local VSTIMD=... STATEMACHINED=... TRIALD=..."; \
	  exit 1; \
	fi

test-local: check-checkouts ## against local checkouts (VSTIMD, STATEMACHINED, TRIALD)
	uv sync --quiet --group dev
	@# **The override is what makes "all local" possible.** triald declares
	@# `statemachined-client` as a git URL, and installing that same
	@# distribution from a local path as well is two URLs for one package,
	@# which uv refuses outright. That refusal is the same shape as the
	@# lockfile cycle README.md describes -- the one that made the handover
	@# test unrunnable inside statemachined -- and it is right: without saying
	@# which copy wins, a resolver would have to guess.
	@#
	@# So this says. The point of this target is the checkouts, and a run that
	@# quietly fetched the *published* client while building everything else
	@# from source would answer a question nobody asked.
	@printf 'statemachined-client @ file://%s\n' "$(abspath $(STATEMACHINED)/client/python)" > .local-overrides.txt
	@# triald's daemon is Python, installed here with the two clients.
	@# statemachined's daemon is Rust, like vstimd's: built in its checkout and
	@# put first on PATH, with the firmware compiled for this host beside it
	@# as the board-less fixture (contracts/DAEMON_LAYOUT.md).
	uv pip install --quiet --overrides .local-overrides.txt \
		-e "$(TRIALD)/daemon[serve]" \
		-e $(STATEMACHINED)/client/python \
		-e $(VSTIMD)/client/python
	@# An environment from before this installed the Python daemon, whose
	@# console script `uv run` would put ahead of the Rust one on PATH.
	uv pip uninstall --quiet statemachined 2>/dev/null || true
	$(MAKE) -C $(STATEMACHINED) rust integration-device
	VSTIMD_BINARY=$(abspath $(VSTIMD))/target/release/vstimd \
	STATEMACHINED_NATIVE_DEVICE=$(abspath $(STATEMACHINED))/build/statemachined_native_device \
	PATH=$(abspath $(STATEMACHINED))/target/debug:$$PATH \
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
	uv pip install --quiet "$$(grep '^vstimd-client' artifacts/requirements.txt)" \
		artifacts/triald-*.whl artifacts/statemachined_client-*.whl
	uv run --group dev --no-sync pytest -v --hardware \
		--display $(or $(DISPLAY_ADDR),tcp://127.0.0.1:5555) \
		--executor $(or $(EXECUTOR),127.0.0.1:8081) \
		$(ARGS)

# Interactive: both explain what they will do and ask first. See WIRING.md.
accept-docker: ## real board on USB, daemons in Docker; installs nothing here
	./run_on_hardware.py docker $(ARGS)

accept-pi: ## a Raspberry Pi test system with the board attached (runs sudo)
	sudo ./run_on_hardware.py pi $(ARGS)

lint:
	uv run --group dev ruff check .

clean:
	rm -rf .venv artifacts .pytest_cache .ruff_cache .local-overrides.txt
	-docker rmi $(IMAGE)
