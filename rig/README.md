# rig/ — the tests that belong to no daemon

The dynamic half of this repository. `INTERACTIONS.md` writes down what the
daemons promise each other and `check_outcomes.py` proves each repo's sources
still say it; these tests run all three and watch them do it.

vstimd renders. statemachined runs a trial's state machine. triald decides what a
trial is and what its outcome was. Each has its own suite, and each of those
suites is about that daemon. This directory holds the ones that are about
*the three together* — and they live here because such a test has no honest home
inside any of them.

It owns no source. That is the point, and it is the same point the rest of the
repository makes.

## Why not inside one of the daemons

The obvious place was statemachined, where the stage-2 test already lives:
triald is installed there for the end-to-end test, and the expensive fixture —
the firmware built for the host — is there too.

But that test only reaches for triald, which is the one daemon allowed to know
about the others. Reaching for **vstimd** from statemachined would mean
statemachined's CI building a Rust renderer for a daemon it has never heard of.
Its own test file says so plainly: *"this daemon knows nothing about triald — no
client, no base URL, no schema, no outbound call of any kind."* The same is true
of vstimd, and a test-only dependency is still a line in a lockfile, a build in
CI, and a thing a maintainer has to keep green.

The other candidate was triald, which is the only daemon that knows both. The
code under test really is triald's. But triald's CI would then have to build a
Rust binary *and* a firmware image, neither of which it needs for anything else,
and the honest description of that repo would stop being "a trial daemon".

So: not in any of them — here, next to the contract they are testing against.
The cost is real: pins that go stale quietly, and a CI job heavy enough to want
its own path filter. It is smaller than putting two foreign builds into a daemon
that should not know the other two exist.

## What it installs, and why that matters

**Released artifacts, not checkouts.** This is the difference between testing
the code and testing what a person actually gets: the wheel that was built, the
binary that was signed, the entry point that the packaging declares. A rig
integration test that builds from source proves the tree works and says nothing
about the thing installed on a rig.

`rig_versions.toml` pins one release of each daemon. Bumping a pin is the
deliberate act of saying these three versions work together, which is the only
claim this repo makes.

### The bootstrap gap, honestly

**Two of the three now ship what that needs; none of it is released yet.** As of
now:

| | released? | what is missing |
|---|---|---|
| vstimd | v0.1.0 | 0.2 is tagged-but-unpushed, and 0.1 has no event stream at all |
| statemachined | v0.1.0-alpha1 | the released `.deb` predates the shipped device; the code has it |
| triald | — | packaging exists and is unreleased: no tag has been cut |

So every fixture here resolves in three steps, in order:

1. **The pinned release artifact** — the intended path, and the only one that
   tests what an operator installs.
2. **An installed daemon, or an environment variable naming a local build** —
   `VSTIMD_BINARY`, `STATEMACHINED_SRC`. For developing against an unreleased
   change, and for getting this repo working at all today.
3. **Skip, naming exactly what was missing.** Never a silent pass.

Step 2 is scaffolding, not the design. What each repo owes, to close it:

* **vstimd** — release 0.2. The pipeline already publishes binaries; the tags are
  prepared. This is the only one of the three where the *code* is not yet enough.
* **statemachined** — ✅ **done, unreleased.** The package now carries the
  firmware compiled for the host at
  `/opt/braemons/statemachined/libexec/statemachined-device`, and
  `statemachined device` puts it on a port. So an installed daemon has a device,
  and the socket bridge is `statemachined.device.native_device_on_a_socket` —
  an ordinary import rather than a path into somebody's checkout. Needs a tag.
* **triald** — ✅ **done, unreleased.** `nfpm` config, packaging Makefile, pinned
  builder image and `release.yml`, publishing `.deb`, `.rpm` **and a wheel**. The
  wheel is the one that matters here: these tests import triald rather than
  running it, and until it existed the only way to have it was a git URL. Needs
  a tag.

### Nothing here imports a daemon

Except the two libraries an experiment script imports: `triald` and
`vstimd-client`. The daemons themselves are **processes on ports**, started the
way their service units start them and talked to over HTTP and ZeroMQ.

That was not true at first. The executor fixture built statemachined in-process
with Starlette's `TestClient`, which is exactly how statemachined's own suite
tests it — correctly, because there the daemon is the subject. Here it is not:
what is under test is a rig, and on a rig this daemon is a service on port 8081
that nothing imports. Reaching into it as a library exercises a path no operator
has, and it also quietly avoided the real client: `StateMachineExecutor` now runs
over httpx and a real WebSocket, the way it ships.

## Running it

```bash
cd rig
make test         # against pinned releases — what an operator installs
make test-local   # against local checkouts (VSTIMD, STATEMACHINED, TRIALD)
```

From the repository root, `make rig` does the same.
