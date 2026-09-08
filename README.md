# braemons contracts

How the braemons daemons tie together: the interactions between them, and the
handful of facts they have to agree on.

Every other repo in the family is a thing that runs —
[vstimd](https://github.com/braemons/vstimd) renders,
[triald](https://github.com/braemons/triald) decides,
[statemachined](https://github.com/braemons/statemachined) times,
[console](https://github.com/braemons/console) shows them on one screen. This
one runs nothing. It holds the contract they meet at.

> **Status: a plan and an argument.** Most of what
> [`INTERACTIONS.md`](INTERACTIONS.md) describes is not built. It is meant to be
> argued with before any of it is.

## Read this first

**[`INTERACTIONS.md`](INTERACTIONS.md)** — the catalogue. Every call between two
daemons, its direction, its payload, and whether it exists yet. Plus the three
defects in the one interaction that *is* wired up, where the end-to-end tests
should live, and what is still undecided.

vstimd's `proto/vstimd/v1/` can be read start to finish and it tells you the
whole client-facing surface. Nothing played that role for the interactions
*between* daemons: they were spread across two FastAPI apps, one protobuf tree,
a serial protocol and a shared-memory layout, written down together only as a
diagram repeated in three different `PLAN.md` files. `INTERACTIONS.md` is the
document you read to review them.

## Why this is a separate repo

None of the three daemon repos can own a cross-repo contract without inverting a
dependency that is currently clean: statemachined knows triald's schema, triald
knows nothing of statemachined, and vstimd knows neither. Putting the catalogue
in triald would make triald the hub. Putting it in console would put domain logic
in the one repo whose rule is that it has none.

**console and contracts are the two "how they fit together" repos, and they
answer different questions.** console is how a rig looks on one screen; contracts
is what the daemons say to each other on the wire.

## What this repo is not

**Not a place to put shared code.** No client library, no generated stubs, no
package any daemon imports at build time. A daemon that cannot build without
this repo is not optional any more, and every daemon being independently
buildable is the property the whole architecture is arranged around.

The files here are **vendored, not depended on**: a repo copies
`outcomes.json` in and adds a test that its copy still matches. Drift becomes a
red CI run instead of a rejected graph at 2 a.m. `INTERACTIONS.md` §7 has the
long form of this argument, including why it is a data file and not a `.proto`.

## What lands here next

| | |
|---|---|
| `outcomes.json` | the eleven `.tdr` outcome codes, as `(name, value)` — the source of truth for the five copies that have already drifted |
| `generate.py` | `outcomes.json` → a C++ `enum class`, a Python `IntEnum`, a JS array. Optional, and the only way to reach the firmware's copy |
| `mdns.md` | the TXT record keys every braemons daemon publishes, and the `rig=` salt that lets a console tell which daemons are one rig |

Order of work is `INTERACTIONS.md` §10.
