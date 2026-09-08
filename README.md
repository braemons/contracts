# braemons contracts

How the braemons daemons tie together: the interactions between them, and the
handful of facts they have to agree on.

Every other repo in the family is a thing that runs —
[vstimd](https://github.com/braemons/vstimd) renders,
[triald](https://github.com/braemons/triald) decides,
[statemachined](https://github.com/braemons/statemachined) times,
[console](https://github.com/braemons/console) shows them on one screen. This
one runs nothing. It holds the contract they meet at.

> **Status: the shape is settled and most of it is built.** `make test-e2e` in
> statemachined runs one whole trial across two daemons, with triald driving.
> What is left is listed in [`INTERACTIONS.md`](INTERACTIONS.md) §10.

## Read this first

**[`INTERACTIONS.md`](INTERACTIONS.md)** — the catalogue. Every message between
two daemons, its direction, its payload, and whether it exists yet. Plus the
defects that were found by writing it down, where the end-to-end tests live, and
what is still undecided.

vstimd's `proto/vstimd/v1/` can be read start to finish and it tells you the
whole client-facing surface. Nothing played that role for the interactions
*between* daemons: they were spread across two FastAPI apps, one protobuf tree,
a serial protocol and a shared-memory layout, written down together only as a
diagram repeated in three different `PLAN.md` files. `INTERACTIONS.md` is the
document you read to review them.

## Who knows about whom

**One daemon knows the others, and it is triald.** That is not an accident and
not a smell — it is what a decision authority is. Everything else is a
participant, and a participant that knew who was watching would have acquired a
consumer's problems.

| | knows about | how |
|---|---|---|
| **vstimd** | nobody | renders; broadcasts what it saw on a PUB socket |
| **statemachined** | nobody | runs a trial; writes what it did to its trace |
| **triald** | both | commands them, and subscribes to what they publish |
| **console** | all of them | shows them on one screen, and decides nothing |

The rule underneath it: **a participant publishes what it observed and commands
nobody; a decision authority commands its participants and subscribes to what
they publish.**

Both participants were built the other way round first, and both were wrong for
the same reason. statemachined held a `triald_base_url` and posted each outcome
to it; vstimd was going to answer per-trial questions about frame loss. A
participant cannot know whether a consumer exists, or should, or is running a
session — so **only a consumer can tell "not yet" from "never"**, and directing a
message at a named one makes the participant responsible for a delivery it
cannot reason about. It also buys it a config setting, a copy of somebody else's
schema, a failure path and a retry policy, for a fact it should simply have
stated.

What that buys is the property the whole family is arranged around: **every
participant runs with nothing else on the network.** Not a degraded mode — it is
what a bench box does all day, and it is why the interfaces stay small. There is
nothing to configure about a consumer that has no name.

## Why this is a separate repo

Not because triald must not be a hub — it is one, for *running* a rig, and
`INTERACTIONS.md` argues that is correct. It is because the things in here are
not triald's.

The `.tdr` outcome taxonomy lives in five copies across two repos, including one
in firmware that will never link a Python package. statemachined needs it to
compile a graph on a bench with no triald anywhere. Putting the canonical copy
in triald would make every other repo depend on the *decision authority* to know
a shared vocabulary — which is a build dependency on the one daemon most likely
to be absent.

Putting the catalogue in console would put domain logic in the one repo whose
rule is that it has none.

**console and contracts are the two "how they fit together" repos, and they
answer different questions.** console is how a rig looks on one screen; contracts
is what the daemons say to each other on the wire.

## Two halves: what is promised, and whether it is kept

A contract nobody checks is a wish, and a pile of assertions with no written
contract is a test suite nobody can argue with. So both live here:

| | |
|---|---|
| **static** | `INTERACTIONS.md` says what the daemons promise each other. `check_outcomes.py` reads each repo's own sources — Python enums, a C++ header, a JavaScript table — and holds them to `outcomes.json`. It runs offline, inside each repo's CI, against a vendored copy. |
| **dynamic** | [`rig/`](rig/README.md) installs all three daemons and runs a trial across them. It checks the things no static reader can: that the frame axis vstimd publishes is the axis triald bounds a trial with, that a trial run on one daemon can be joined to what another saw while it ran. |

They fail differently, which is why both are worth having. A renamed outcome
breaks the checker in every repo within seconds and needs no daemon running. A
frame counter that quietly drifted from another frame counter passes every static
check ever written, and only three real processes on one machine will say so.

## What this repo is not

**Not a place to put shared code.** No client library, no generated stubs, no
package any daemon imports at build time. A daemon that cannot build without
this repo is not optional any more.

That rule binds the static half. `rig/` is the exception that proves it: it
installs all three daemons, and it can, because **nothing installs `rig/`**. It
is downstream of everything and upstream of nothing.

The files here are **vendored, not depended on**: each repo holds a
byte-identical copy and checks its own sources against it, offline, in its own
CI. Nothing reaches for this repository at build time — that would be a build
dependency wearing a disguise, failing on a day this repo was unreachable for
reasons having nothing to do with that one. `INTERACTIONS.md` §6 and §7 have the
long form, including why it is a data file and not a `.proto`.

## What is here

| | |
|---|---|
| [`INTERACTIONS.md`](INTERACTIONS.md) | the catalogue, the argument, and the order of work |
| `outcomes.json` | the `.tdr` outcome codes — the source of truth for five copies that had already drifted |
| `check_outcomes.py` | vendored into each repo; reads that repo's own sources and holds them to the table |
| `check_vendored_copies.py` | are the copies still this one? `--fix` syncs them. Run here, by whoever changes the taxonomy |
| [`rig/`](rig/README.md) | the three-daemon end-to-end tests, and the pinned releases they run against |

## What lands here next

| | |
|---|---|
| `mdns.md` | the TXT record keys every braemons daemon publishes, and the `rig=` salt that lets a console tell which daemons are one rig |

Order of work is `INTERACTIONS.md` §10.
