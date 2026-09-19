.PHONY: help vendored e2e e2e-local

# Two halves, and they cost very different amounts to run. `check` is a few
# regexes over some source files and finishes before you let go of the key;
# `e2e` builds a Rust renderer and a firmware image. Keeping them as separate
# targets — and separate CI jobs with path filters — is what stops the cheap one
# from inheriting the slow one's runtime.

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# There is no `check` target holding the daemons' sources to the taxonomy, and
# that is not an omission. Each repo has its own `make check-proto`, which reads
# its own sources against its own vendored copy, offline. Nothing here reaches
# into another repo to check it — that would be the build dependency this whole
# arrangement exists to avoid.
#
# What can only be done here is the other direction: are those copies still this
# one? Whoever changes the taxonomy is standing in this directory anyway.
vendored: ## are the vendored copies still this one? (ARGS=--fix syncs them)
	python3 check_vendored_copies.py $(ARGS)

e2e: ## the three-daemon tests, against pinned releases
	$(MAKE) -C e2e-tests test ARGS="$(ARGS)"

e2e-local: ## the same, against local checkouts
	$(MAKE) -C e2e-tests test-local ARGS="$(ARGS)"
