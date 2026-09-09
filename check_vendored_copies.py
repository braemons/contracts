#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Are the vendored copies of `outcomes.json` still this one?

Vendoring buys the family a great deal — no shared build dependency, no release
to cut for a one-field change, and a repo that never syncs keeps working — and
it costs exactly one thing: nothing makes the copies match. This is that one
thing, and it is deliberately *not* automatic.

**It runs here and nowhere else.** A repo checking its own vendored copy against
a remote canonical one would be a build dependency wearing a disguise: it would
need the network, and it would fail on a day this repository was unreachable for
a reason having nothing to do with that repo. So each daemon checks its own
sources against the copy it holds, in its own CI, offline — and this script is
for the person changing the taxonomy, who is standing here anyway.

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

#: Where each repo keeps its copy. A repo that does not appear here has no copy
#: to keep — vstimd has no outcome table, and console has no domain logic.
VENDORED = [
    FAMILY / "triald" / "tests" / "contracts",
    FAMILY / "statemachined" / "python" / "tests" / "contracts",
]

FILES = ["outcomes.json", "check_outcomes.py"]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="copy the canonical files over")
    arguments = parser.parse_args()

    stale: list[Path] = []
    for directory in VENDORED:
        if not directory.is_dir():
            print(f"missing: {directory} — is that repo checked out beside this one?")
            continue
        for name in FILES:
            canonical, copy = HERE / name, directory / name
            if not copy.exists() or copy.read_bytes() != canonical.read_bytes():
                stale.append(copy)

    if not stale:
        print(f"every vendored copy matches ({len(VENDORED)} repos, {len(FILES)} files each)")
        return 0

    for copy in stale:
        print(f"stale: {copy.relative_to(FAMILY)}")
    if not arguments.fix:
        print("\nrun with --fix to copy this repository's versions over them,")
        print("then run each repo's own tests: they check their sources, not the file.")
        return 1

    for copy in stale:
        shutil.copyfile(HERE / copy.name, copy)
        print(f"updated: {copy.relative_to(FAMILY)}")
    print("\nnow run each repo's tests. A copy that is newer than its sources is")
    print("how a taxonomy change gets half-applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
