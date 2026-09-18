# braemons contracts

How the braemons daemons fit together: the messages between them, the facts they
have to agree on, and the end-to-end tests that check they do.

| repo | role |
|---|---|
| [vstimd](https://github.com/braemons/vstimd) | renders stimuli; publishes what it drew on a PUB socket |
| [statemachined](https://github.com/braemons/statemachined) | runs a trial's state machine; publishes what it did on its trace |
| [triald](https://github.com/braemons/triald) | decides what a trial is and records its outcome |
| [console](https://github.com/braemons/console) | shows a rig on one screen, and decides nothing |

This repo runs nothing on a rig. It holds the contract the daemons meet at.

## Who knows about whom

| | knows about | how |
|---|---|---|
| **vstimd** | nobody | renders; broadcasts what it saw |
| **statemachined** | nobody | runs a trial; writes what it did to its trace |
| **triald** | both | commands them, and subscribes to what they publish |
| **console** | all of them | shows them on one screen |

**A participant publishes what it observed and commands nobody; whatever runs
the experiment commands the participants and subscribes to what they publish.**
That is triald for a daily session, or a single Python script for a bench or a
pilot. Either way every participant runs with nothing else on the network, and
only the consumer that is waiting can tell "not yet" from "never".

## What is here

| | |
|---|---|
| [`INTERACTIONS.md`](INTERACTIONS.md) | the catalogue: every message between two daemons, its direction and payload, and what is still open |
| [`DAEMON_LAYOUT.md`](DAEMON_LAYOUT.md) | the shape every daemon repository takes, and the rule that its public interface is hand-authored protobuf |
| `outcomes.json` | the `.tdr` outcome codes, the source of truth for every copy of the table |
| `check_outcomes.py` | vendored into each repo; reads that repo's own sources and holds them to `outcomes.json` |
| `check_vendored_copies.py` | checks the vendored copies still match this one; `--fix` syncs them |
| [`e2e-tests/`](e2e-tests/README.md) | end-to-end tests across all three daemons, and the pinned releases they run against |
| [`e2e-tests/WIRING.md`](e2e-tests/WIRING.md) | the physical rig the hardware tests assume, and how to run them on it |

## Two halves

| | |
|---|---|
| **static** | `INTERACTIONS.md` says what the daemons promise each other. `check_outcomes.py` reads each repo's sources (Python enums, a C++ header, a JavaScript table) and holds them to `outcomes.json`. It runs offline, in each repo's CI, against a vendored copy. |
| **dynamic** | `e2e-tests/` installs the released packages of all three daemons, starts them as an operator would, and runs experiments across them: through triald, and through a single script with no triald. It checks what no static reader can, such as that vstimd's frame axis is the one a trial is bounded by. |

A renamed outcome breaks the static checker in every repo within seconds. A frame
counter that drifted from another only shows up with real processes running, so
both halves are needed.

## Running

```bash
make vendored          # are the vendored copies still this one? (ARGS=--fix)
make e2e               # end-to-end tests in a container, against pinned releases
make e2e-local         # the same, against local checkouts
```

On a wired rig: `cd e2e-tests && make accept`. See [`e2e-tests/WIRING.md`](e2e-tests/WIRING.md).

## What this repo is not

**Not shared code.** No client library, no generated stubs, no package any daemon
imports. The static files are vendored: each repo holds a byte-identical copy and
checks its sources against it offline, so no build ever reaches for this repo.
`e2e-tests/` installs all three daemons, and can, because nothing installs it.
