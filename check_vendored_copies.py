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
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAMILY = HERE.parent

#: Where each repo keeps its copy. A repo that does not appear here has no copy
#: to keep — vstimd has no outcome table, and console has no domain logic.
#:
#: **triald is no longer here.** Its taxonomy is a protobuf enum now
#: (`INTERACTIONS.md` §6), which is the thing this JSON file was imitating, and
#: it holds its own copies to that enum in its own tests. statemachined takes
#: the same enum when it is migrated, and then this list — and these two files —
#: go away entirely.
VENDORED = [
    FAMILY / "statemachined" / "python" / "tests" / "contracts",
]

FILES = ["outcomes.json", "check_outcomes.py"]

#: The enum that replaced the JSON, and the JSON that has not gone yet.
#:
#: While one daemon has migrated and the other has not, two file formats state
#: the same taxonomy and nothing else compares them. That is precisely the shape
#: of the defect §5.2 records, so the transition gets its own check rather than
#: a note asking somebody to remember.
ENUM = FAMILY / "triald" / "proto" / "triald" / "v1" / "outcomes.proto"

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

    if not stale and not taxonomy_problems():
        print(f"every vendored copy matches ({len(VENDORED)} repos, {len(FILES)} files each)")
        print("and outcomes.json still says what triald's enum says")
        return 0

    mismatched = taxonomy_problems()
    for problem in mismatched:
        print(problem)

    if mismatched and not stale:
        print("\nthe two formats of the taxonomy disagree. The enum is the one that")
        print("is right; outcomes.json is on its way out.")
        return 1

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


def taxonomy_problems() -> list[str]:
    """Do the enum and the JSON still state the same taxonomy?

    Both are read as text: the enum because nothing here may depend on protobuf,
    the JSON because it is JSON. Silent when either file is absent, which is
    what a checkout of one repository looks like.
    """
    if not ENUM.is_file():
        return []
    canonical = HERE / "outcomes.json"
    if not canonical.is_file():
        return []

    body = re.search(r"enum TrialOutcome\s*\{(.*?)^\}", ENUM.read_text(), re.S | re.M)
    if body is None:
        return [f"{ENUM.name}: no enum TrialOutcome this reader can see"]
    from_enum = {
        name: int(value)
        for name, value in re.findall(
            r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+)\s*;", body.group(1), re.M
        )
    }
    from_json = {
        entry["name"]: entry["value"] for entry in json.loads(canonical.read_text())["outcomes"]
    }

    problems = []
    for name in sorted(set(from_json) - set(from_enum)):
        problems.append(f"outcomes.json has {name}, which triald's enum does not")
    for name in sorted(set(from_enum) - set(from_json)):
        problems.append(f"triald's enum has {name}, which outcomes.json does not")
    for name in sorted(set(from_enum) & set(from_json)):
        if from_enum[name] != from_json[name]:
            problems.append(
                f"{name} is {from_json[name]} in outcomes.json and "
                f"{from_enum[name]} in triald's enum"
            )
    return problems


if __name__ == "__main__":
    sys.exit(main())
