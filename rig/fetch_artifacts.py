#!/usr/bin/env python3
"""Fetch the pinned release packages into `artifacts/`.

Here rather than inside the container so that a run never reaches the network
for its inputs: the image is built from files on disk, the same files locally and
in CI, and a slow or unreachable GitHub fails at a step that says so instead of
halfway through a docker build.

**Asset names are checked, not guessed.** An earlier version of this repository
carried invented names in `rig_versions.toml` — plausible, wrong, and harmless
looking, because the code that read them fell through to a local path when
nothing matched. Every release branch of that code went unexecuted and nobody
could tell. So: if a pin names an asset the release does not have, this says so
and lists what the release does have.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

HERE = pathlib.Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"


def assets_of(repo: str, tag: str) -> list[str]:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "assets",
         "--jq", ".assets[].name"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return []
    return [line for line in result.stdout.splitlines() if line]


def fetch(repo: str, tag: str, pattern: str) -> bool:
    have = assets_of(repo, tag)
    if not have:
        print(f"  ✗ {repo} has no release {tag}")
        return False
    result = subprocess.run(
        ["gh", "release", "download", tag, "--repo", repo,
         "--pattern", pattern, "--dir", str(ARTIFACTS), "--clobber"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(f"  ✗ {repo} {tag}: nothing matching {pattern!r}")
        print(f"    it publishes: {', '.join(have)}")
        return False
    print(f"  ✓ {repo} {tag}: {pattern}")
    return True


def main() -> int:
    pins = tomllib.loads((HERE / "rig_versions.toml").read_text())
    ARTIFACTS.mkdir(exist_ok=True)

    ok = True
    for name, entry in pins.items():
        repo = entry.get("repo")
        pattern = entry.get("asset")
        if not repo or not pattern:
            continue
        ok &= fetch(repo, entry["tag"], pattern.format(version=entry["version"]))

    # What the test venv installs. Kept beside the .debs so the image copies one
    # directory and the whole input set is visible in one place.
    requirements = [
        f"{entry['pypi']}=={entry['pypi_version']}"
        for entry in pins.values()
        if entry.get("pypi")
    ]
    requirements += ["pytest>=8.3", "httpx>=0.28", "websockets>=13"]
    (ARTIFACTS / "requirements.txt").write_text("\n".join(requirements) + "\n")
    print(f"  ✓ requirements.txt: {', '.join(requirements)}")

    if not ok:
        print(
            "\nSome pinned artifacts do not exist. This is the bootstrap gap, not "
            "a bug here — see rig/README.md for what each repo still owes."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
