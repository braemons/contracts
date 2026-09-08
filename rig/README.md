# rig-integration

**The tests that belong to no daemon.**

vstimd renders. statemachined runs a trial's state machine. triald decides what a
trial is and what its outcome was. Each has its own suite, and each of those
suites is about that daemon. This repo holds the ones that are about *the three
together* — and it exists because such a test has no honest home inside any of
them.

It owns no source. That is the point.

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

So: a fourth repo, with the cost that comes with it — no source of its own, a CI
to keep green, and version pins that go stale quietly. Those are real. They are
smaller than putting two foreign builds into a daemon that should not know the
other two exist.

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

**None of the three currently ships what that needs.** As of now:

| | released? | what is missing |
|---|---|---|
| vstimd | v0.1.0 | 0.2 is unreleased, and 0.1 has no event stream at all |
| statemachined | v0.1.0-alpha1 tag | no published artifact; and the host-native device is a test fixture, not a release asset |
| triald | — | no release workflow |

So every fixture here resolves in three steps, in order:

1. **The pinned release artifact** — the intended path, and the only one that
   tests what an operator installs.
2. **An environment variable naming a local build** — `VSTIMD_BINARY`,
   `STATEMACHINED_DEVICE`. For developing against an unreleased change, and for
   getting this repo working at all today.
3. **Skip, naming exactly what was missing.** Never a silent pass.

Step 2 is scaffolding, not the design. What each repo owes, to close it:

* **vstimd** — release 0.2; the pipeline already publishes binaries.
* **statemachined** — publish the native device (`firmware/native/`) as a release
  asset. It is already built for its own tests; nothing else needs writing.
* **triald** — a release workflow. It has none.

## Running it

```bash
make test         # the three-daemon tests
make test-local   # the same, against local builds (see the env vars above)
```
