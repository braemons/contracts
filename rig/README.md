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

### The bootstrap gap, closed

This section used to explain why none of the three shipped what these tests
need. All of them do now, and `make test` is green on nothing but published
artifacts:

| | release | what it publishes for this suite |
|---|---|---|
| vstimd | `v0.2.0-alpha1` | the server `.deb`, and `vstimd-client 0.2.0a1` on PyPI — the first client with an event stream |
| statemachined | `v0.2.0-alpha1` | the daemon `.deb`, carrying the firmware compiled for the host at `libexec/statemachined-device` |
| triald | `v0.2.0-alpha1` | the daemon `.deb`, **and a wheel** — these tests import triald rather than run it |

Fixtures still resolve in three steps, and the order is the point:

1. **The pinned release artifact** — the intended path, and the only one that
   tests what an operator installs.
2. **An installed daemon, or an environment variable naming a local build**
   (`VSTIMD_BINARY`, `STATEMACHINED_SRC`). For developing against an unreleased
   change.
3. **Skip, naming exactly what was missing.** Never a silent pass.

Step 2 is for developing, not for CI. What `make test` runs never reaches it.

**What closing the gap cost, and what it found.** Two of the three needed real
work rather than a tag: statemachined's package had no device in it, so a box
that installed it had a daemon and nothing to point at; triald had a systemd
unit and no way to build anything at all, and the unit it shipped passed
`--config` a rig config for a flag that takes a session config JSON — a command
line that could never have started the daemon, unnoticed because nothing had
ever run it.

Then the container found a third thing neither repo's CI could: statemachined
ships `expected_board = "uno_r4_minima"` and the packaged device reports
`native`, so the daemon refused it — correctly, since a line map addressing
another board's pins is how a rig rewards the wrong animal. Saying which board
is on the wire is an operator's step, and `container/entrypoint.sh` does it.

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
