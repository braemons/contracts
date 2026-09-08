# The braemons daemon interactions

> **Status: plan, and an argument.** Nothing in §6–§9 is built. §3 is a
> catalogue of what exists today, with the gaps marked — it is meant to be
> checked against the code, not trusted.
>
> **Why this file exists.** vstimd's `proto/vstimd/v1/` can be read start to
> finish and it tells you the whole client-facing surface. Nothing plays that
> role for the interactions *between* daemons: they are spread across two
> FastAPI apps, one protobuf tree, a serial protocol and a shared-memory
> layout, and the only place they were ever written down together is a diagram
> in three different PLAN.md files. This is the document you read to review
> them.

## 1. Where this lives

A fourth location, deliberately, and the smallest one that can work.

None of the three repos can own a cross-repo contract without inverting a
dependency that is currently clean: statemachined knows triald's schema, triald
knows nothing of statemachined, and vstimd knows neither. Putting the catalogue
in triald would make triald the hub; putting it in console would put domain
logic in a repo whose whole rule is that it has none.

**What this location is for:** the catalogue below, and the shared *vocabulary*
in §6. **What it is emphatically not:** a place to put shared code. See §7.

## 2. The rig, and the three interactions

```
                    ┌──────────────────────────────────────┐
                    │ triald    decides · counts · records │   one session
                    └──┬──────────────┬───────────────┬────┘
        (A) push trial │   (B) push   │   (C) ask     │
        HTTP+JSON      │   outcome    │   frame loss  │
                       ▼   HTTP+JSON  │   ZMQ+proto   ▼
              ┌────────────────┐◀─────┘        ┌──────────────┐
              │ statemachined  │               │    vstimd    │   always on
              └───────┬────────┘               └──────┬───────┘
                      │ USB CDC · NDJSON              │
              ┌───────┴────────┐              ┌───────┴────────┐
              │    firmware    │              │  VTL shm ⇄ daqd│
              └───────┬────────┘              └───────┬────────┘
                      └───────── TTL, the fast bus ───┘
```

Three interactions on the slow bus, and their directions are now settled:

| | | Direction | Rate |
|---|---|---|---|
| **A** | triald → statemachined | **push**, per trial | once per trial |
| **B** | statemachined → triald | **push**, per trial | once per trial |
| **C** | triald → vstimd | **pull**, per trial | once per trial |

**B is a push, and the alternative is now dead code.** Both loops exist in the
tree today: `statemachined/daemon/src/statemachined/triald_client.py` posts the
outcome (push), while `triald/src/triald/behaviour.py`'s `BehaviourSource` has
`arm()` then a blocking `result(trial_id)` (pull), and `runner.run_trial()`
calls the pull one. Only the simulated path uses it. See §9.1.

**C is a pull, and that is the asymmetry worth noticing.** statemachined names
the outcome, so it has something to say the moment a trial ends. vstimd only
accumulates a fact — whether a frame was missed — that triald needs at the same
moment. Making vstimd push it would mean vstimd learning the trial loop; asking
for it costs one round trip inside the ITI, which is already where the other
round trips hide.

**Everything time-critical is absent from this picture, and that is the point.**
Stimulus onset reaching the state machine, and the state machine's response
reaching vstimd, are TTL edges through `gpiochip-daqd` and the VTL shared-memory
segment. They are not interactions in this catalogue and must never become
ones: USB CDC costs ~2 ms per message on the R4 (`statemachined/dev/HARDWARE.md`
§ "Scan rate and link cost"), against a frame quantum of 8.3 ms at 120 Hz.
The fast bus carries what must be *timed*; the slow bus carries what must be
*recorded*.

## 3. The catalogue

### A — triald configures a trial on statemachined

`POST http://<statemachined>/api/trial/configure` → `/start`

Built on statemachined's side (`api/trial_routes.py`). **Not built on triald's
side: triald has no outbound client module at all.**

```
ConfigureTrialRequest         # extra="forbid"
  trial_id              int   ≥ 0
  graph                 str   a name, never a slot; "" means the active graph
  cap_milliseconds      int   ≥ 0, wall-clock cap on the whole trial
  start_source          str   "serial" | "ttl"                        (§9.3)
  distribution_patches  [ { index, minimum_ms, maximum_ms,
                            mean_ms, duration_ms } ]

POST /api/trial/start   { trial_id }   → refused unless armed for that id
POST /api/trial/cancel  { trial_id }
GET  /api/trial/result                 → the last completed trial
```

**The trial type is not on this wire and never will be.** The device receives a
graph, timings and a reward duration; it cannot tell a go trial from a catch
trial. That omission is what keeps firmware stable while paradigms change.

**triald can name a graph** (settled, §9.2, built). `TrialType.graph` is a
name, carried through `TrialSpec` into the record and out through
`TrialParameters.graph`. It replaced `TrialType.time_sequence`, a bare index
triald never read: an index points at a different machine the moment the
executor's store is edited, which is the disease sets were cured of in VStim
#239. Empty means "leave whatever is loaded".

**triald holds no graphs and validates nothing about the name.** The executor
owns the store and refuses a name it does not have — a configuration error
triald can report, rather than a trial that quietly ran the wrong machine. Where
statemachined keeps the store is its own business, and the name never reaches
the firmware as a name: the daemon resolves it.

**Still missing: triald's outbound client.** Nothing in triald posts to
`/api/trial/configure` yet; `TrialParameters` is handed to a `BehaviourSource`,
and the only implementation is the simulator. §10 item 5.

### B — statemachined reports the outcome to triald

`POST http://<triald>/api/trial/outcome`

Built on both sides. **Broken — see §5.1.**

```
OutcomeReport                       # triald/dev/API.md
  outcome              int | name   the .tdr code; 1 and "HIT" both accepted
  manipulandum         int | name
  reaction_time_ms     float?
  terminating_interval int?
  precise_fixation     bool         FROM THE EYE MONITOR — can veto on its own
  frame_loss           {interval, frame}?   FROM VSTIMD — can veto on its own
  reward_ms            int          what was actually delivered
  hit_condition        bool
  simulated            bool
  note                 str?
                                    ← trial_id MISSING. §5.1
```

**statemachined fills in six of these and leaves the rest at their defaults,
deliberately.** It has never heard of the eye monitor or of vstimd. Filling in
`precise_fixation` or `frame_loss` with a plausible value would make it a second
decision authority. The reply says whether the outcome was *accepted* as well as
counted, and why not when it was not.

`simulated: false`, always, and not as a formality: `triald sim` produces
outcomes with it true, and a rig whose records cannot be told apart from a
simulator's is a rig whose data cannot be trusted.

### C — triald asks vstimd what happened during the trial

**Not built on either side, and it needs a design decision before an endpoint.**

vstimd counts dropped frames in `server/src/timing.rs:61` and warns about them in
`render/render_frame.rs:893`. Nothing crosses the wire, there is no per-trial or
windowed accounting, and vstimd has no concept of a trial at all —
`timing.rs:47` carries the whole gap as a comment: *"Flag the trial if timing
precision matters."*

What triald needs is one field, `frame_loss: {interval, frame} | null`, which is
already in its schema and is currently always null on a real rig.

Two shapes, and the choice is a real one:

1. **vstimd learns `trial_id`.** triald opens a window (`BeginTrial(trial_id)`),
   vstimd tags dropped frames with it, triald asks for that trial's tally. Exact,
   and it puts a trial concept into a daemon that has stayed free of one.
2. **vstimd stays trial-blind and answers about frames.** triald notes the frame
   counter at configure, asks for drops since frame *N* at result. Keeps vstimd's
   vocabulary, and the accounting is triald's — which is where accounting lives
   everywhere else in this stack.

**Recommendation: (2).** It costs vstimd one message and no new concept, and it
matches the division everywhere else: participants report what they measured,
triald decides what it meant. It also degrades honestly — a rig that never asks
gets no frame-loss veto and knows it, rather than silently getting `null`.

Either way this is a new message in `proto/vstimd/v1/system.proto`, because
protobuf over ZMQ is already vstimd's wire and a bolt-on REST endpoint beside it
would be a second surface for one field.

### The fast bus, for completeness

Not interactions in the sense above — no request, no reply, no schema to review
— but they are how the daemons actually couple, and a reviewer looking for
"where does stimulus onset reach the state machine" should find the answer here.

| | |
|---|---|
| `vstimd/vtl/` | the shared-memory layout: 4 input banks, 1 output bank, `u64` each, rise/fall latches, drained once per frame at frame start |
| `gpiochip-daqd` | VTL ⇄ `/dev/gpiochipN`. Input edges from kernel events, outputs mirrored onto pins |
| `statemachined/dev/PROTOCOL.md` | USB CDC, NDJSON, CRC, indices-not-names. The daemon ⇄ firmware link |

## 4. What is built

| Step | Where | |
|---|---|---|
| triald picks a trial | `session.next_trial()`, `POST /api/trial/next` | ✅ |
| triald names the graph for it | `TrialType.graph` → `TrialSpec` → `TrialParameters` | ✅ |
| triald configures statemachined | — | ❌ no client in triald |
| statemachined arms the device | `POST /api/trial/configure` | ✅ |
| start · cancel · result | `api/trial_routes.py` | ✅ |
| device runs the trial | firmware, `device_supervisor`, native build on a socket | ✅ |
| statemachined reports back | `triald_client.py` | ⚠️ §5.1 |
| triald counts, accepts, records | `session.report_outcome()`, `recording.py` | ✅ |
| triald configures vstimd | — | ❌ neither side |
| triald asks vstimd for frame loss | — | ❌ neither side, §3C |
| readiness gate `configure→ready→start` | designed, `triald/dev/PLAN.md` | ❌ not built |

## 5. Three defects, all in interaction B

### 5.1 `trial_id` — a 422 on every trial

`triald_client.py` sends `trial_id`. triald's `OutcomeReportModel`
(`api/schemas.py:325`) has no such field and its base `Model` is
`extra="forbid"` (`schemas.py:68`). `triald/dev/API.md` — which the client's
docstring says it transcribes — does not list it either. **The one call
statemachined makes outwards cannot succeed today.**

It is a design gap and not a typo. `session.report_outcome()`
(`session.py:292`) takes the outcome of "the trial in flight" and has no id to
check it against, while `behaviour.py:52` calls trial identity *"the whole
correctness story… what stops a late outcome being attributed to the trial after
it, which is how a rig quietly mislabels a dataset"* — a guard that exists only
on the simulated pull path.

**Fix: add `trial_id: int` to triald's `OutcomeReport`, required, and refuse a
report whose id is not the trial in flight.** Update `dev/API.md` in the same
change.

**Why nobody noticed** is the argument for §8 in one file:
`tests/unit/test_triald_client.py:70` asserts `sent["trial_id"] == 193` against
an `httpx.MockTransport` that returns 200 for anything. The mock is the far end,
so the test validates statemachined's *belief* about triald's schema.

### 5.2 The outcome taxonomy has drifted

Code 8 is spelled two ways across five copies:

| Copy | Spelling |
|---|---|
| `statemachined/firmware/core/trial/trial.h:31` | `InexpectedStartSignal` |
| `triald/src/triald/outcomes.py:53` | `INEXPECTED_START_SIGNAL` |
| `triald/src/triald/web/app.js:106` | `INEXPECTED_START_SIGNAL` |
| `statemachined/daemon/.../model/trial_outcome.py:36` | `UNEXPECTED_START_SIGNAL` |
| `statemachined/.../graph_store_panel_element.js:59` | `UNEXPECTED_START_SIGNAL` |

The *values* agree, so nothing on the wire is wrong today. Two things break
anyway: `triald_client.py` sends `result.outcome.name`, so that one outcome is
unparseable at the far end; and graphs name outcomes by string —
`model/graph_definition.py:335` validates against `DECLARABLE_TERMINAL_OUTCOMES`,
keyed by name — so a graph authored from triald's vocabulary is refused at
compile, by a person with both spellings in front of them in two web UIs.

**Fix: pick one, then §6 so it cannot happen again.** `trial.h:19` claims to
mirror VStim's `TDR.h`, and it and triald agree on `Inexpected`, so the typo is
almost certainly original. **Proposal: the typo wins** — it is a wire contract
with years of `.tdr` files behind it, and statemachined's daemon and its panel
change. Confirm against `VStimLib/TDR.h`.

### 5.3 `reaction_time_ms` int vs float

statemachined sends `int`, triald declares `float | None`. Coerces cleanly. No
action; recorded so the next reader does not re-derive it.

## 6. The shared vocabulary — data, not an IDL

Four facts are genuinely shared between daemons. **None of them is an RPC
message**, which is the whole reason §7 says no to a proto repo.

| Fact | Today |
|---|---|
| the eleven `.tdr` outcome codes | five copies, drifted (§5.2) |
| rig identity — a `rig=` TXT record salted `braemons:` | designed in `console/docs/PLAN.md` §4, built nowhere |
| mDNS TXT keys — `id` `version` `api` `elements` `device` `port` | statemachined publishes all six; vstimd publishes `id` only, pointing at the ZMQ port |
| VTL bit and line semantics | already a proper in-repo contract in `vstimd/vtl/` — leave it there |

The first three become files here:

```
conventions/
├── INTERACTIONS.md     this document
├── outcomes.json       the eleven (name, value) pairs — the source of truth
├── mdns.md             the TXT keys, and the rig= salt
└── generate.py         optional: outcomes.json → C++ header, Python enum, JS array
```

**Each repo vendors the file and tests its own copy against it.** There is
already precedent for exactly this in the tree:
`statemachined/daemon/tests/unit/wire_vectors.json` is vendored golden data
doing the same job.

**Vendoring, not a package dependency.** A daemon that cannot build without
this repo is not optional any more, and every daemon being independently
buildable is the property the whole architecture is arranged around. A vendored
file plus a CI check gets the drift caught without the build dependency.

**Why a data file rather than protobuf, specifically.** The firmware holds one
of the five copies. The RA4M1 has 32 KB of SRAM with ~11 KB unclaimed, and
`PROTOCOL.md` §6 deliberately puts *no names* on that wire — states, lines and
distributions are integer indices — to avoid spending it. It will never link
protobuf. A `.proto` file would therefore exclude the copy that is hardest to
fix; `generate.py` emitting an `enum class` covers it.

For the `rig=` record, note that `console/docs/PLAN.md` §4 already reaches the
same conclusion by a different route — its preferred fix is *"a few lines in
each daemon"*, not a shared library. `mdns.md` is where those few lines agree
on what to publish.

## 7. Why not one protobuf/gRPC repo for everything

The idea is right about the problem — the interactions are unreviewable — and
wrong about the remedy. Recorded here so it is not re-proposed.

- **It reintroduces the coupling the architecture exists to avoid.** Every
  daemon build-depends on it; changing one field means cutting a release and
  every repo's CI having an opinion. "Small and optional interfaces" does not
  survive a shared build dependency.
- **The transports are different for good reasons.** vstimd's ZMQ+protobuf is
  earned: high rate, binary, clients in Python, MATLAB and C++ needing generated
  types. (Note it is ZMQ, *not* gRPC — `service.proto:228`'s service block is
  marked "for future gRPC transport" and is unused.) triald ⇄ statemachined is
  one call per trial, both ends Python, and HTTP+JSON there is a commitment:
  curl-able, hand-writable, and `schemas.py:375` exists so a scientist can send
  `"HIT"` instead of `1`.
- **The schemas cannot be authored in proto today.** triald's and
  statemachined's OpenAPI is *generated from* FastAPI/Pydantic. Inverting that
  means rewriting both APIs around generated types to fix one missing field.
- **The firmware cannot consume it.** §6.

**What replaces it for reviewability is this document plus §8's conformance
test.** The catalogue is what you read; the test is what stops it becoming
fiction.

### The narrower version, which is worth doing

`triald`'s OpenAPI document already *is* the machine-readable schema for
interaction B. `triald_client.py:32` says of its own model *"A transcription,
not a design"* — and the transcription is where §5.1's bug is. So:

- **Now:** a test in statemachined that fetches triald's OpenAPI schema for
  `OutcomeReport` and asserts its own model's fields are a subset with matching
  types. ~20 lines, no codegen, no build step, and it fails on drift forever.
- **Later, optional:** generate the client model with `datamodel-code-generator`
  and commit the output. Stronger, but it is a build step in repos with a
  no-build-step culture, and the test above catches the same class of bug.

## 8. End-to-end tests

**Where: `statemachined/daemon/tests/integration/`.** The dependency points
statemachined → triald, dev-only, from git — which matches the coupling that
already exists and keeps triald standalone-testable. Not console (no domain
logic), not a new repo yet (§8 stage 3).

The harness is already there and is better than it needs to be:
`tests/integration/conftest.py` runs the real firmware core as a host build
behind `bench/native_device_on_a_socket.py`, and
`test_http_api_against_native_device.py` drives the whole daemon API against it.
triald is a pip-installable FastAPI app, so it mounts **in-process** via
`httpx.ASGITransport` — no subprocess, no port, no fixture teardown race.

### Stage 1 — one whole trial, two daemons *(needs only §5.1 and §5.2)*

`test_a_whole_trial_with_triald.py`: real triald ASGI app + native device on a
socket + a graph from `statemachined/graphs/`.

1. arm a triald session, `POST /api/trial/next`
2. `POST /api/trial/configure` on statemachined with that `trial_id` and a graph
3. `POST /api/trial/start`, drive the device's input lines to a terminal state
4. assert the outcome reached triald with the right `trial_id`, the right
   `.tdr` code, `simulated: false`, and the acceptance triald reports back
5. **the negative that matters:** a report for the wrong `trial_id` is refused

Steps 2 and 4 go through the *push* path, which `runner.run_trial()` does not
use — so this is also the first test of the loop a rig actually runs.

### Stage 2 — triald initiates

Trial types can name a graph now (§9.2). Once triald has an outbound client,
flip the test so triald drives all three steps. This is the loop as described in
§2.

### Stage 3 — vstimd

Once §3C exists. vstimd needs a real binary (`--null` — ZMQ only, no display),
so it is a subprocess and a heavier fixture: three daemons, two languages, a
Rust build in CI. **That is the point at which a small `rig-integration` repo
starts to earn itself**, and not before.

## 9. Open decisions

1. **Push or pull for the outcome — settled as push; the pull path should go.**
   `BehaviourSource`'s `arm`/`result` stays as the *simulator's* seam (that is
   what makes `triald sim` exercise the real accounting), but `runner.run_trial()`
   calling it is the real trial loop only by accident. Decide whether
   `run_trial` becomes simulator-only or is replaced by the API path.
2. ~~**Where do graphs live in triald?**~~ **Settled: nowhere.** A trial type
   carries a graph *name* and nothing more; the graphs themselves live in the
   executor's store. triald has no opinion about where one sits in that store
   and never validates the name — an index would be a second, silent identity
   for the same thing, and triald owning graph bodies would make it the hub the
   family is built to avoid. Built in triald (`TrialType.graph`, replacing
   `time_sequence`). What remains of item 5 is the outbound client.
3. **`start_source`.** `configure` takes `"serial"` or `"ttl"`. On a rig it
   should be `ttl` so reaction times need no clock sync; `serial` is the
   desk-testing path. Confirm the default per deployment, and whether triald
   ever sets it.
4. **The readiness gate.** `triald/dev/PLAN.md` designs
   `configure → ready → start` across every registered participant. With two
   participants and one of them trial-blind, is the gate worth building now, or
   is "configure returned 200" the readiness answer until there is a third?
5. **Which spelling of code 8 wins** (§5.2). Check `VStimLib/TDR.h`.
6. **§3C shape 1 or 2** — does vstimd learn `trial_id`, or stay trial-blind?

## 10. Order of work

| | | Blocked on |
|---|---|---|
| 1 | `outcomes.json` + `generate.py`; settle §9.5; regenerate all five copies | §9.5 |
| 2 | `trial_id` on triald's `OutcomeReport`, required, and refused when it is not the trial in flight | — |
| 3 | The OpenAPI conformance test in statemachined (§7) | 2 |
| 4 | **Stage 1 e2e** | 1, 2 |
| 5 | ~~Answer §9.2; a `graph` on the trial type~~ **done**; triald's outbound client | — |
| 6 | **Stage 2 e2e** — triald initiates | 5 |
| 7 | `mdns.md`; `rig=` in both daemons; vstimd's TXT records and web port | — |
| 8 | §3C: the vstimd message, and per-trial or windowed frame-loss accounting | §9.6 |
| 9 | **Stage 3 e2e**, and decide whether `rig-integration` exists | 8 |

Items 2, 3, 7 are independent and small. Item 1 is the one that stops a class of
bug rather than a bug. Item 5 is the one with a design decision inside it.
