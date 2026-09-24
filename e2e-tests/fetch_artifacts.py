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
import shutil
import subprocess
import sys
import tomllib

HERE = pathlib.Path(__file__).resolve().parent
ARTIFACTS = HERE / "artifacts"

#: Where container/Dockerfile copies the directory above. requirements.txt names
#: any local wheel by this path rather than by a host one, because pip resolves
#: a relative path against its own working directory and a host path does not
#: exist in the image at all.
CONTAINER_ARTIFACTS = "/tmp/artifacts"


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

    # Emptied, not merely created. `gh release download --clobber` overwrites a
    # file of the same name and says nothing about one whose name has changed --
    # so a bumped pin, or a package that was renamed, leaves the *previous*
    # artifact sitting here and the image installs both. That is not
    # hypothetical: braemons-statemachined 0.2 Conflicts with the statemachined
    # 0.1 beside it, and the build failed with "held broken packages" rather
    # than with anything naming the stale file.
    #
    # What is in this directory afterwards is exactly what the pins name.
    if ARTIFACTS.exists():
        shutil.rmtree(ARTIFACTS)
    ARTIFACTS.mkdir()

    ok = True
    for entry in pins.values():
        repo = entry.get("repo")
        pattern = entry.get("asset")
        if not repo or not pattern:
            continue
        ok &= fetch(repo, entry["tag"], pattern.format(version=entry["version"]))
        # statemachined's board-less far end, a binary beside its package.
        if entry.get("native_device_asset"):
            ok &= fetch(repo, entry["tag"], entry["native_device_asset"])
        # A wheel published as a release asset rather than to a registry. Same
        # fetch, same directory: the image copies one directory and the whole
        # input set is visible in one place.
        if entry.get("wheel"):
            ok &= fetch(
                repo, entry["tag"], entry["wheel"].format(wheel_version=entry["wheel_version"])
            )

    # What the test venv installs. Kept beside the .debs so the image copies one
    # directory and the whole input set is visible in one place.
    requirements = [
        f"{entry['pypi']}=={entry['pypi_version']}"
        for entry in pins.values()
        if entry.get("pypi")
    ]
    # A wheel fetched above, named by the path it will have *inside the image*
    # rather than here. The container copies this directory to /tmp/artifacts,
    # so a relative path would resolve against pip's working directory and a
    # host path would not exist there at all.
    requirements += [
        f"{CONTAINER_ARTIFACTS}/{entry['wheel'].format(wheel_version=entry['wheel_version'])}"
        for entry in pins.values()
        if entry.get("wheel")
    ]
    # httpx and websockets are gone with statemachined's routes: nothing in
    # this suite speaks HTTP any more, and the two streams are
    # server-streaming rpcs carried by the client libraries above.
    requirements += ["pytest>=8.3"]
    (ARTIFACTS / "requirements.txt").write_text("\n".join(requirements) + "\n")
    for line in requirements:
        print(f"  ✓ requirements.txt: {line}")

    if not ok:
        print(
            "\nSome pinned artifacts do not exist. This is the bootstrap gap, not "
            "a bug here — see e2e-tests/README.md for what each repo still owes."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
