#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Are the vendored copies of `braemons/v1/` still this one?

Vendoring buys the family a great deal — no shared build dependency, no release
to cut for a one-field change, and a repo that never syncs keeps working — and
it costs exactly one thing: nothing makes the copies match. This is that one
thing, and it is deliberately *not* automatic.

**It runs here and nowhere else.** A repo checking its own vendored copy
against a remote canonical one would be a build dependency wearing a disguise:
it would need the network, and it would fail on a day this repository was
unreachable for a reason having nothing to do with that repo. So each daemon
checks its own sources against the copy it holds, in its own CI, offline — and
this script is for the person changing the taxonomy, who is standing here
anyway.

**What is vendored is now a `.proto`.** It was `outcomes.json` plus a
`check_outcomes.py` that read source code with regexes, and those are gone:
both daemons take `braemons/v1/trial_outcome.proto`, which is the thing that
JSON file was imitating — numbers that are never reused, names that are a wire
contract, and a format two languages generate from rather than parse.

`braemons.v1` rather than either daemon's package because neither owns the
taxonomy: statemachined reports an outcome and triald records one
(`DAEMON_LAYOUT.md` §2). It is canonical *here* for the same reason.

Usage:

    ./check_vendored_copies.py            # the sibling repos beside this one
    ./check_vendored_copies.py --fix      # copy this one over them
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAMILY = HERE.parent
CANONICAL = HERE / "vendored" / "proto"

#: Where each repo keeps its copy, relative to the repo root. A repo that does
#: not appear here has no copy to keep — vstimd has no outcome table, and
#: console has no domain logic.
VENDORED = {
    "triald": Path("proto"),
    "statemachined": Path("proto"),
}

#: What is vendored, relative to `CANONICAL` and to each repo's copy.
FILES = [Path("braemons/v1/trial_outcome.proto")]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="copy the canonical files over")
    arguments = parser.parse_args()

    stale: list[tuple[Path, Path]] = []
    checked = 0
    for repository, prefix in VENDORED.items():
        root = FAMILY / repository / prefix
        if not (FAMILY / repository).is_dir():
            print(f"missing: {repository} — is that repo checked out beside this one?")
            continue
        for name in FILES:
            canonical, copy = CANONICAL / name, root / name
            checked += 1
            if not copy.exists() or copy.read_bytes() != canonical.read_bytes():
                stale.append((canonical, copy))

    if not stale:
        print(f"every vendored copy matches ({len(VENDORED)} repos, {checked} files)")
        return 0

    for _, copy in stale:
        print(f"stale: {copy.relative_to(FAMILY)}")
    if not arguments.fix:
        print("\nrun with --fix to copy this repository's versions over them,")
        print("then run each repo's own tests: they check their sources, not the file.")
        return 1

    for canonical, copy in stale:
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, copy)
        print(f"updated: {copy.relative_to(FAMILY)}")
    print("\nnow run each repo's `make check-proto`. A copy that is newer than its")
    print("sources is how a taxonomy change gets half-applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
