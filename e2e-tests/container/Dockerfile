# A rig, as an operator builds one: install the packages, edit the config, start
# the services.
#
# **Nothing is built from source here, and nothing is pip-installed from a
# checkout.** The whole question this image asks is whether the artifacts the
# release publishes work together — which is a different question from whether
# the branches do, and the only one an operator can act on. If a test fails
# here and passes in a developer's checkout, the packaging is wrong, and that is
# exactly the failure nobody else in the family can see.
#
# Ubuntu because that is what the .debs target. The .rpm side wants its own
# image and its own job; see e2e-tests/README.md.
FROM ubuntu:24.04

# ca-certificates for PyPI; the rest is what the daemons' packages depend on.
# `--no-install-recommends` on purpose: a rig box is minimal, and a test that
# quietly relied on a recommended package would pass here and fail there.
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
      ca-certificates python3 python3-pip python3-venv \
 && rm -rf /var/lib/apt/lists/*

# ── The packages ──────────────────────────────────────────────────────────────
#
# Fetched by `make artifacts` on the host, from the pinned releases, so this
# image never reaches the network for them and the versions cannot drift between
# a local run and CI.
COPY artifacts/*.deb /tmp/artifacts/
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends /tmp/artifacts/*.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/artifacts/*.deb

# ── The client libraries ──────────────────────────────────────────────────────
#
# The two things an experiment script imports, installed the way a script's
# author installs them. Not the daemons: those are services now, and nothing
# here imports them.
#
# One comes from PyPI and one is a wheel from a GitHub release, and
# requirements.txt names both -- triald is not on a registry, so its wheel is
# fetched beside the .debs and installed from /tmp/artifacts. `--no-index` is
# deliberately *not* set: vstimd-client is on PyPI and this is what a script's
# author would run.
COPY artifacts/requirements.txt /tmp/requirements.txt
COPY artifacts/*.whl /tmp/artifacts/
RUN python3 -m venv /opt/e2e-tests \
 && /opt/e2e-tests/bin/pip install --quiet --no-cache-dir -r /tmp/requirements.txt \
 && rm -rf /tmp/artifacts

# ── The tests ─────────────────────────────────────────────────────────────────
COPY tests /opt/e2e-tests/tests
COPY container/entrypoint.sh /usr/local/bin/e2e-entrypoint
RUN chmod +x /usr/local/bin/e2e-entrypoint

ENTRYPOINT ["/usr/local/bin/e2e-entrypoint"]
