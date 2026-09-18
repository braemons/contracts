# The braemons daemon interactions

> **Status: the shape is settled and half of it is built.** §5.1, §5.2, §8's
> stage 1 and §9.1 are done — `make e2e-local` here runs one whole trial across
> both daemons, with triald driving. §6 is not built, and §3C
> (vstimd) is now an open issue rather than a design here. §3 is a catalogue of
> what exists today, with the gaps marked — it is meant to be checked against
> the code, not trusted.
>
> **The one thing to read if you read nothing else: §2's rule.** Every
> interaction below now follows from it, and two of the three were rewritten
> when it was stated.
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

**One daemon knows the others, and it is triald** — that is what a decision
authority is (§2). vstimd and statemachined know nobody: they publish what they
observed and command nothing.

So the catalogue could go in triald, and it should not. The things here are not
triald's. The `.tdr` taxonomy lives in five copies across two repos, one of them
firmware that will never link a Python package, and statemachined needs it to
compile a graph on a bench with no triald anywhere — putting the canonical copy
in triald would make every other repo depend on the *decision authority* to know
a shared vocabulary, which is a build dependency on the daemon most likely to be
absent. Putting the catalogue in console would put domain logic in a repo whose
whole rule is that it has none.

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
| **A** | triald → statemachined | **command**, per trial | once per trial |
| **B** | statemachined ⇢ triald | **broadcast**, subscribed | every event |
| **C** | vstimd ⇢ triald | **broadcast**, subscribed | every event |

A fourth participant is designed but unbuilt — mousewheeld, the locomotion
input, with rows **D**, **E** and **F** in §3. It changes nothing about the
three above, which is the test the rule is meant to pass.

### The rule the three now follow

**A participant publishes what it observed and commands nobody. A decision
authority commands its participants and subscribes to what they publish.**

B and C were both designed the other way and both were wrong for the same
reason. A participant cannot know whether a consumer exists, or should, or is
running a session — statemachined's `triald_base_url` could be stale config for
a box that was decommissioned. Directing a message at a named consumer makes the
participant responsible for a delivery it cannot reason about, and buys it a
config setting, a copy of somebody else's schema, a failure path and a retry
policy, for a fact it should simply have stated.

**Only a consumer can tell "not yet" from "never."** So the deadline belongs to
whoever is waiting, and the interpretation belongs to whoever is deciding.

What this buys, and it is the whole point: **every participant runs with nothing
else on the network.** That is not a degraded mode, it is what a bench box does
all day, and it is why the interfaces stay small — there is nothing to configure
about a consumer that has no name.

**B is a broadcast, and `triald_client.py` is gone.** statemachined has no
client, no `triald_base_url`, no copy of triald's schema and no outbound call of
any kind. Every trial's result goes into its trace with everything else it did,
and `WS /api/trace/stream` is where anything reads it — lossless, and it names
the entries a slow consumer lost rather than handing it a shorter answer that
looks complete. `GET /api/trace/trial/{id}` recovers any trial exactly.

`triald/src/triald/behaviour.py`'s `BehaviourSource` — `arm()` then a blocking
`result(trial_id)` — stays as the **simulator's** seam and nothing else. On a
rig, implementing it would mean blocking in a loop asking "is trial 7 done yet"
for an event that was already published. See §9.1.

**C is a broadcast, and follows B** — built on vstimd's `0.2` branch,
`braemons/vstimd#145`. `proto/vstimd/v1/events.proto` is the artefact to read;
`--event-port` (5556) and `--no-events` are the switches. It was designed as a
pull: triald notes the frame counter at configure and asks for "drops since
frame N" at result. That would work. It has two costs a stream does not:

- **frame loss becomes the only thing anybody can ever learn.** Every further
  fact — stimulus onset, a VTL edge, a present that missed — needs its own
  request/response pair designed, named and versioned. A stream needs one more
  message type.
- **it is traffic on the REP socket**, the same one that carries scene commands,
  dispatched under a write lock on `SceneState`. Cheap per trial, but it is
  contention on the path that must not stall.

**The asymmetry that made it look like a pull is real, and it survives.** vstimd
only accumulates a *fact* — whether a frame was missed — while statemachined
names an *outcome*. That is why vstimd must stay trial-blind (§9.6): it
broadcasts frame-numbered facts and never learns what a trial is. **The consumer
owns the join**, because the consumer is the only one that knows.

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

**triald can name a graph** (settled, §9.2, built). `TrialType.statemachine_graph`
is a name, carried through `TrialSpec` into the record and out through
`TrialParameters.statemachine_graph`. It replaced `TrialType.time_sequence`, a bare index
triald never read: an index points at a different machine the moment the
executor's store is edited, which is the disease sets were cured of in VStim
#239. Empty means "leave whatever is loaded".

**triald holds no graphs and validates nothing about the name.** The executor
owns the store and refuses a name it does not have — a configuration error
triald can report, rather than a trial that quietly ran the wrong machine. Where
statemachined keeps the store is its own business, and the name never reaches
the firmware as a name: the daemon resolves it.

**The field is spelled differently on each side, deliberately.** triald calls it
`statemachine_graph`, because in triald's vocabulary a bare "graph" says nothing
about whose it is and triald holds none of its own. statemachined calls it
`graph`, because its namespace supplies the rest. A client maps the one field;
renaming either would break a working API to buy symmetry nobody reads.

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
| `vstimd/vinput/` | the second shared-memory layout: a seqlock, `f64` axis values and a writer heartbeat, read once per frame. Where a wheel or an eye tracker reaches the camera (§3 D–F) |

### D, E, F — mousewheeld, a fourth participant

**Proposed, not agreed here yet.** The design is `mousewheeld/dev/PLAN.md`;
these rows are in this catalogue so that a reviewer looking for "how does the
wheel reach the camera" finds an answer rather than silence. Nothing about the
daemon is built — but half of F is, on vstimd's side, which is why it is worth
writing down before the rest is.

mousewheeld reads a running-wheel encoder and publishes how far the animal went.
It is a **participant** under §2's rule: no `vstimd_address`, no
`triald_base_url`, no outbound call of any kind.

| | | Direction | Rate |
|---|---|---|---|
| **D** | triald → mousewheeld: place a mark, arm a zone set | command | per trial |
| **E** | mousewheeld ⇢ triald: the path since a mark, zone hits | read / subscribed | per trial, or every event |
| **F** | mousewheeld ⇢ vstimd: `vinput` shared memory | **fast bus** | every sample |

**F is not an interaction in this catalogue's sense, and must never become
one.** vstimd needs the position *inside* the frame it is drawing: shared memory
costs <1 µs with <1 µs of jitter, where ZMQ PUB on localhost costs 20–80 µs with
spikes past 200 µs — which at 240 Hz is a randomly dropped frame
(`vstimd/dev/INPUT_LATENCY.md` §2). So the producer writes a segment whose
layout vstimd defines, vstimd names a device in its rig config and never learns
who writes it, and neither has a client of the other. Exactly the arrangement
`gpiochip-daqd` and VTL already have. A camera on another host is served by a
relay process that subscribes and writes local shm, not by teaching vstimd to
subscribe.

**Zone TTLs do not appear above, deliberately.** "The animal has run 200 cm" is
decided on the device, in the scan that sees the count, and leaves as a TTL edge
into statemachined and daqd — the fast bus, like stimulus onset. Routing an
outcome that must be *timed* through the slow bus is the mistake §2's last
paragraph exists to prevent. triald records the hit afterwards, over E.

## 4. What is built

| Step | Where | |
|---|---|---|
| triald picks a trial | `session.next_trial()`, `POST /api/trial/next` | ✅ |
| triald names the graph for it | `TrialType.statemachine_graph` → `TrialSpec` → `TrialParameters` | ✅ |
| triald configures statemachined | `StateMachineExecutor.configure` | ✅ |
| statemachined arms the device | `POST /api/trial/configure` | ✅ |
| start · cancel · result | `api/trial_routes.py` | ✅ |
| device runs the trial | firmware, `device_supervisor`, native build on a socket | ✅ |
| statemachined publishes the result | the trace, `WS /api/trace/stream` | ✅ |
| triald subscribes and translates | `triald.executor`, `api/statemachine_executor.py` | ✅ |
| …over statemachined's own client | `statemachined.client`, `statemachined/python/` | ✅ |
| statemachined lists who is watching | `GET /api/observers`, Observers panel | ✅ |
| triald counts, accepts, records | `session.report_outcome()`, `recording.py` | ✅ |
| triald configures vstimd | — | ❌ neither side |
| vstimd broadcasts frame loss | `WS`-less: ZMQ PUB, `events.proto` | ✅ `0.2` |
| triald joins frame loss to a trial | `triald.stimulus` | ✅ |
| a client subscribes to the stream | `vstimd.events` (vstimd-client) | ✅ |
| triald wires the two together | — | ❌ the last gap |
| readiness gate `configure→ready→start` | designed, `triald/dev/PLAN.md` | ❌ not built |
| **F** vstimd reads a position device | `vstimd/vinput/`, `input/devices.rs`, `[[input.device]]`, `LinearNav3D` | ✅ `0.3`, consumer half |
| **F** anything writes one | — | ❌ no producer exists |
| **D**, **E** mousewheeld itself | `mousewheeld/dev/PLAN.md` | ❌ a plan, M0–M7 all open |

## 5. Three defects, all in interaction B

**5.1 and 5.2 are fixed.** Kept in full because the fix is only half the
value — the other half is why neither was visible, which is what §7 and §8 are
for. Marked ✅ where they were closed.

### 5.1 `trial_id` — a 422 on every trial ✅ **fixed**

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

**Fixed** in triald (`graph-by-name`): `trial_id` is required on
`OutcomeReportModel`, checked against the trial in flight, and refused with 409
if it is any other one — never accepted, because triald cannot tell which of two
reports is the truth. It addresses the message and is deliberately *not* on
`OutcomeReport` itself or in the record, which already carries the trial's
number; two copies could disagree. `Session.report_outcome` takes it as an
optional keyword, since in-process callers hold the spec they are answering and
cannot be late. `dev/API.md` and the web UI updated in the same change.

**Why nobody noticed** is the argument for §8 in one file:
`tests/unit/test_triald_client.py:70` asserted `sent["trial_id"] == 193` against
an `httpx.MockTransport` that returns 200 for anything. The mock is the far end,
so the test validated statemachined's *belief* about triald's schema. **A mock
at the far end of a contract tests one side's opinion of the contract twice.**
That test still exists and is still a mock — it is the right tool for "what does
this daemon decide to send" — but it now says so, and what the far end accepts is
the e2e suite's job.

### 5.2 The outcome taxonomy has drifted ✅ **fixed**

Code 8 is spelled two ways across five copies:

| Copy | Spelling |
|---|---|
| `statemachined/firmware/core/trial/trial.h:31` | `InexpectedStartSignal` |
| `triald/src/triald/outcomes.py:53` | `INEXPECTED_START_SIGNAL` |
| `triald/src/triald/web/app.js:106` | `INEXPECTED_START_SIGNAL` |
| `statemachined/python/.../model/trial_outcome.py:36` | `UNEXPECTED_START_SIGNAL` |
| `statemachined/.../graph_store_panel_element.js:59` | `UNEXPECTED_START_SIGNAL` |

The *values* agree, so nothing on the wire is wrong today. Two things break
anyway: `triald_client.py` sends `result.outcome.name`, so that one outcome is
unparseable at the far end; and graphs name outcomes by string —
`model/graph_definition.py:335` validates against `DECLARABLE_TERMINAL_OUTCOMES`,
keyed by name — so a graph authored from triald's vocabulary is refused at
compile, by a person with both spellings in front of them in two web UIs.

**Fixed: the correct spelling wins.** `VStimLib/TDR.h:33` does declare
`InexpectedStartSignal`, but **the value is the wire contract, not the name.**
8 is what a `.tdr` holds and what an analysis script reads; nothing in this
family needs to read a name VStim wrote, and there is no backwards
compatibility to keep with it. Inheriting the typo would mean carrying a
misspelling in two web UIs, an acceptance flag and every graph file for as long
as the family exists.

So all five copies now say `UNEXPECTED_START_SIGNAL`: triald's enum, its
acceptance flag (`unexpected_start_signal`, on the wire too), its web UI,
statemachined's firmware header, its core test, its daemon and its graph
panel.

An assertion holds them there:
`python/tests/unit/test_the_outcome_report_matches_trialds_schema.py` compares
statemachined's `TrialOutcome` to triald's, name and value, so the next drift is
a failing test rather than a graph nobody can compile. §6 would still be better —
this only covers two of the five copies.

### 5.3 `reaction_time_ms` int vs float

statemachined sends `int`, triald declares `float | None`. Coerces cleanly. No
action; recorded so the next reader does not re-derive it.

## 6. The shared vocabulary — data, not an IDL

Four facts are genuinely shared between daemons. **None of them is an RPC
message**, which is the whole reason §7 says no to a proto repo.

| Fact | Today |
|---|---|
| the `.tdr` outcome codes | 🔄 **now a protobuf enum**, `triald/proto/triald/v1/outcomes.proto`. statemachined still holds the JSON |
| rig identity — a `rig=` TXT record salted `braemons:` | designed in `console/docs/PLAN.md` §4, built nowhere |
| mDNS TXT keys — `id` `version` `api` `elements` `device` `port` | statemachined publishes all six; vstimd publishes `id` only, pointing at the ZMQ port |
| VTL bit and line semantics | already a proper in-repo contract in `vstimd/vtl/` — leave it there |

The first three become files here:

```
contracts/
├── INTERACTIONS.md            this document
├── outcomes.json              ✅ the (name, value) pairs — the source of truth
├── check_outcomes.py          ✅ vendored into each repo; reads its sources
├── check_vendored_copies.py   ✅ are the copies still this one? --fix syncs them
└── mdns.md                    the TXT keys, and the rig= salt
```

**A checker, not a generator — the plan said `generate.py` and that was
wrong.** These tables are four fifths prose, and the prose is the part with
value: *why* code 8 is spelled correctly, why two codes may not be declared, who
may assign `NEVER_FINISHED`. Generating them deletes exactly that, or forces it
into a JSON field nobody reads in context. So the copies stay hand-written where
a person will read them, and `check_outcomes.py` makes them one table. It also
means no build step, no generated files and no "do not edit" headers — a
strictly smaller thing than what §6 originally proposed.

**Each repo vendors the taxonomy and tests its own copies against it**, offline,
in its own CI. triald does this against the enum, in
`triald/tools/check_outcomes.py`; statemachined still holds
`python/tests/contracts/outcomes.json` and takes the vendored enum when it is
migrated. Until then `check_vendored_copies.py` here holds the two *formats* to
each other, so the transition cannot drift in the middle.

**Nothing checks a repo's copy against this one automatically, on purpose.** A
repo reaching for the canonical file would be a build dependency wearing a
disguise: it would need the network and would fail on a day this repository was
unreachable for reasons having nothing to do with that repo.
`check_vendored_copies.py` is run *here*, by the person changing the taxonomy,
who is standing here anyway. `--fix` copies them over; then each repo's own
tests say whether its sources have caught up.

**Vendoring, not a package dependency.** A daemon that cannot build without
this repo is not optional any more, and every daemon being independently
buildable is the property the whole architecture is arranged around. A vendored
file plus a CI check gets the drift caught without the build dependency.

### Why a data file rather than protobuf — and why that argument failed

This section used to end here, with the firmware as the decisive objection: it
holds one of the copies, the RA4M1 has 32 KB of SRAM with ~11 KB unclaimed, it
will never link protobuf, and a `.proto` would therefore exclude the copy that
is hardest to fix.

**The argument does not survive, and the mistake in it is worth keeping.** The
firmware never needed to *link* anything. Only the checker needed to read the
taxonomy, and a checker reads text — it read `enum class` out of C++ with a
regex while holding it to a JSON file, and it reads an `enum` out of a `.proto`
with a regex just as easily. "The firmware cannot consume protobuf" is true and
was never the question; the question was what the *checker* consumes.

So the taxonomy is now a protobuf enum, in
[`triald/proto/triald/v1/outcomes.proto`](https://github.com/braemons/triald).
It is the thing `outcomes.json` was imitating: numbers that are never reused,
names that are a wire contract, and a format that a client generates from rather
than parses. Two daemons need it — statemachined reports an outcome, triald
records one — and it is vendored into statemachined byte-identically, which is
the same arrangement with a better file format.

**What the prose argument above got right survives.** These tables are four
fifths prose and the prose is the part with value; it moved into the enum's
comments, where a generated client carries it too. What did *not* survive is the
claim that a `.proto` would cost the firmware its copy.

Three facts the JSON carried are not in the enum — whether a graph may declare
an outcome, whether it is accepted by default, and who assigns it. Each is used
by exactly one daemon, so each lives where it is used, and each is held to the
enum by `triald/tools/check_outcomes.py`. §5.2's defect was a *name and number*
disagreement, which is exactly what an enum covers.

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
  *(Since decided: that inversion is worth it for its own sake, and is planned
  per repository rather than as one shared repo — see
  [`DAEMON_LAYOUT.md`](DAEMON_LAYOUT.md).)*
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

### The narrower question that kept coming back: gRPC ✅ **decided — yes, for control planes**

Asked when mousewheeld's HTTP API went in, and answered *no* at the time. The
answer has changed, and the reasoning below is kept because the way it changed
is the useful part: not one argument refuted, but **every premise it rested on
altered by a later decision.**

What the table said, and what happened to each row:

| Consumer | The objection | What changed |
|---|---|---|
| **MATLAB** | `webread` is built in; gRPC has nothing native | deprioritised explicitly. MATLAB reaches a rig through the Python client. |
| **A console panel** | generated stubs mean a build step, in repos whose element contract exists to avoid one | **the web UI may have a build step now.** That contract was about a *console* not needing one, and a generated client served by the daemon satisfies it. |
| **triald** | a second client style in a Python codebase that has one | triald's own API is being generated too, so there is one style either way |
| **A rig at 3 a.m.** | `curl /api/state`; `grpcurl` is not installed | **traded deliberately.** Each daemon has a client library and a CLI, and those are the supported way in. |

And one objection that was simply wrong rather than outdated: *gRPC needs a
proxy for browsers*. `tonic-web` translates gRPC-Web in process. There is no
Envoy, no second daemon, and no second port — the spike serves the panels, the
REST API, gRPC, gRPC-Web and reflection from one listener.

**And the positive case, which is stronger than any of the above: these
daemons do not do CRUD.** REST is a good fit for a resource graph, and a rig's
control plane is not one. A trial loop is a sequence of *commands* — arm,
select, report, cancel — and so is arming a zone set, taking a calibration
measurement, or stepping a simulator. The URLs said so all along: **10 of
mousewheeld's 24 routes and 17 of triald's 31 end in a verb**, because there
was no noun to name.

    POST /api/calibration/measure/finish
    POST /api/config/reset-counters
    POST /api/session/recording/pause

Those are rpcs written in a notation for resources. `Calibration.FinishMeasuring`
is what they meant. The remaining routes that *are* nouns — a zone-set store, a
trial-type store, a config — are a small minority, and reading a stored document
is an rpc that happens to be idempotent.

**What tipped it was noticing what was being built instead.** By the time
mousewheeld and triald had their interfaces in proto, the family had also
acquired `braemons.v1.route` (an option binding each rpc to an HTTP path),
`check_routes.py` in two repos (holding routers and rpcs to each other),
`/api/proto` (an endpoint announcing the interface), pinned JSON-mapping
settings with a test file each, hand-rolled WebSocket envelopes, and a
bespoke error-to-status mapping. Every one of those is a reimplementation of
something gRPC ships, each individually justified, and the pattern is the
argument.

Measured on the spike (`mousewheeld`, branch `spike/grpc`):

- reflection lists all five services and every rpc, with argument and return
  types and which are streaming, to a client that has never seen the `.proto`.
  That is what `/api/proto` was imitating.
- a server stream is an associated type on a generated trait, not a hand-written
  loop; three frames were read off `WatchWire` by a client built only from what
  reflection said.
- an rpc with no implementation is a compile error, which is the job
  `check_routes.py` was doing by reading source code.
- the prose in the `.proto` arrives as documentation on the generated methods.

Costs, also measured: the release binary grows 115 MB → 142 MB with debug
symbols; `grpcio` has manylinux **aarch64** wheels, so a Python daemon's
vendored interpreter takes it the way it takes fastapi. The one new thing in
the packaging pipeline is a build step for the web UI.

**So: control planes become gRPC — statemachined, triald and mousewheeld.**
What does *not* change:

- **vstimd stays ZMQ.** It is time-critical, its clients decode a high-rate
  stream, and nothing about gRPC helps there. §7's first table is still right
  about it.
- the fast bus — `vinput`, `vtl` — is shared memory and is not an RPC at all.
- the device wire stays NDJSON + CRC to the board.

Two findings from the spike worth carrying into the work:

- an rpc named `Connect` collides with the generated client's own
  `connect(dst)` constructor, so `Device.Connect` needs a different name.
- reflection is a *bidirectional* streaming rpc and gRPC-Web cannot do bidi, so
  a browser cannot use reflection. A client library can.

### And the same move in the other direction ✅ **done**

The transcription ran both ways. triald's `api/statemachine_executor.py` held
*its* copy of statemachined's paths, refusal shape and stream rules — a second
description of that daemon's API, maintained in this family's other repository,
right until the day it was not.

It is now `statemachined.client`, which ships from the repository that owns the
API — in the same distribution as the daemon, so the client and the API it
describes come from one commit — is tested against that daemon in three suites,
one of them starting `statemachined serve` as a subprocess, and is what triald
depends on. What is left in triald is what is genuinely triald's:
`statemachine_graph` → `graph`, and a refusal → `ExecutorError`.

**triald depends on the distribution named `statemachined`, and that is not a
dependency on the daemon.** The package is tiered: the base is the documents
(`model/`) and the HTTP client, `[device]` adds a serial port, `[serve]` adds
the daemon. triald takes the base — pydantic, httpx, websockets. §2's rule is
untouched: the decision authority knows its participants, and they know nobody.

The same restructure gave a bench script a second way in, `statemachined.device`,
which drives a board with no daemon at all. It is deliberately *not* an
interaction in this catalogue: nothing else is on the network when it runs, and
that is what makes it a bench.

Note the asymmetry, and that it is the right one. **statemachined ships a client
and triald does not need to**, because the direction of the relationship is that
statemachined publishes and commands nothing (§2). A `triald-client` would exist
for a console, not for a daemon, and nothing in this catalogue calls triald.

## 8. End-to-end tests

**Where: `statemachined/python/tests/integration/`.** The dependency points
statemachined → triald, dev-only, from git — which matches the coupling that
already exists and keeps triald standalone-testable. Not console (no domain
logic), not a new repo yet (§8 stage 3).

The harness is already there and is better than it needs to be:
`tests/integration/conftest.py` runs the real firmware core as a host build
behind `bench/native_device_on_a_socket.py`, and
`test_http_api_against_native_device.py` drives the whole daemon API against it.
triald is a pip-installable FastAPI app, so it mounts **in-process** via
`httpx.ASGITransport` — no subprocess, no port, no fixture teardown race.

### Stage 1 — one whole trial, two daemons ✅ **built**

[`e2e-tests/tests/test_the_handover_to_triald.py`](e2e-tests/tests/test_the_handover_to_triald.py),
ten tests, plus statemachined's own
`python/tests/unit/test_the_outcome_report_matches_trialds_schema.py` for the
schema half with no device. `make e2e-local`.

1. arm a triald session, `POST /api/trial/next`
2. `POST /api/trial/configure` on statemachined with that `trial_id` and a graph
3. `POST /api/trial/start` — the graph reaches its outcome on a **timeout**, not
   on a lever: the daemon sends commands and never drives the device's inputs,
   so nothing in this process can press anything. Driving lines is
   `tests/hardware/`, with jumper wires.
4. assert the outcome reached triald with the right `trial_id`, the right
   `.tdr` code, `simulated: false`, the veto fields left for whoever owns them,
   and the acceptance triald reports back
5. **the negative that matters:** a report for the wrong `trial_id` is refused
   with 409 and the trial in flight is left untouched
6. and the counterpart: a daemon with `triald_base_url: ""` still runs a trial

Steps 2 and 4 go through the *push* path, which `runner.run_trial()` does not
use — so this is also the first test of the loop a rig actually runs.

**How triald gets there.** Not `httpx.ASGITransport` as first planned — that is
async-only and this call is synchronous. Starlette's `TestClient` *is* an
`httpx.Client` over an ASGI app, so `TrialdClient` takes the client that carries
the request and the test hands it one. Same in-process mount, no subprocess, no
port, no teardown race, public API only.

**It used to live in statemachined**, and moving it here is the whole argument
of `e2e-tests/README.md` reaching its last case. Testing the handover from inside one
of the two daemons meant that daemon installing the other: a dependency group,
a lockfile pin on triald's main branch, and a CI job fetching another repo. When
triald began depending on `statemachined` for the client it had stopped
hand-copying, the two pins closed a cycle uv cannot resolve — so the lock could
not be refreshed, and the suite ran for weeks against a triald commit whose bugs
were already fixed. A test about two daemons belongs to neither.

**A probe that it is not vacuous:** renaming `trial_id` in `triald_client.py`
fails 7 of the 8 — which is exactly §5.1 reproduced.

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

1. ~~**Push or pull for the outcome**~~ **Settled: broadcast, and built.**
   statemachined publishes to its trace and reports to nobody; triald subscribes
   to `WS /api/trace/stream`, pulls a finished trial's events by id, and
   translates them itself. `triald_client.py`, `triald_base_url` and the copy of
   triald's schema are deleted. See §2's rule.

   **`BehaviourSource` is the simulator's seam and nothing else.** Its
   `arm`/`result` is a pull, and on a rig implementing it would mean blocking in
   a loop asking "is trial 7 done yet" for an event already published.
   `runner.run_trial()` is explicitly the simulated loop now. The rig's loop is:
   `next_trial()` → configure the executor → start → *return*; the outcome
   arrives later from the subscription.

   **The interpretation moved with it, and that is half the value.** statemachined
   used to compute `reaction_time_ms` as "the last state a response left" — an
   interpretation of behaviour, made on triald's behalf, by the daemon whose own
   docs say it must never be a second decision authority. It now lives in
   `triald.executor`, which imports nothing and is testable with dictionaries.

   **On an observer registry:** not one that the publisher serves. There is
   nothing to register — opening the socket is subscribing, closing it is
   leaving, and the daemon never acts on the list. It *keeps* one, and shows it
   in its web UI (`GET /api/observers`), purely as a debugging aid: when trials
   stop reaching triald, is nothing connected, or is something connected and
   receiving nothing? Without that the answer is a packet capture. A name is
   self-declared and grants nothing, because there is nothing to grant.

   **Rule of thumb, worth keeping:** broadcast what may be *missed*, and let
   whoever cares hold the deadline. Never make a publisher responsible for a
   delivery it cannot reason about.

2. ~~**Where do graphs live in triald?**~~ **Settled: nowhere.** A trial type
   carries a graph *name* and nothing more; the graphs themselves live in the
   executor's store. triald has no opinion about where one sits in that store
   and never validates the name — an index would be a second, silent identity
   for the same thing, and triald owning graph bodies would make it the hub the
   family is built to avoid. Built in triald (`TrialType.statemachine_graph`,
   replacing `time_sequence`). The outbound client exists too:
   `api/statemachine_executor.py`.
3. **`start_source`.** `configure` takes `"serial"` or `"ttl"`. On a rig it
   should be `ttl` so reaction times need no clock sync; `serial` is the
   desk-testing path. Confirm the default per deployment, and whether triald
   ever sets it.
4. **The readiness gate.** `triald/dev/PLAN.md` designs
   `configure → ready → start` across every registered participant. With two
   participants and one of them trial-blind, is the gate worth building now, or
   is "configure returned 200" the readiness answer until there is a third?
5. ~~**Which spelling of code 8 wins**~~ **Settled: `UNEXPECTED`, the correct
   one.** The *value* is the contract; the name only has to match itself, and
   there is no compatibility to keep with VStim. All five copies now agree.
6. ~~**§3C shape 1 or 2**~~ **Settled: vstimd stays trial-blind**, and the
   broadcast is what makes that work. It publishes frame-numbered facts and
   never learns what a trial is; the consumer owns the join, because the
   consumer is the only one that knows. `braemons/vstimd#145`.
7. ~~**A deadline on the trial in flight**~~ **Settled and built:
   `NEVER_FINISHED = 11`.**

   `SessionConfig.trial_cap_ms` is latched onto the spec at selection and
   published, so the record says what the trial was allowed to take.
   `Session.expire_overdue_trial()` is a no-op unless something is overdue, so
   it is safe on a timer; triald's API runs it for the app's whole life and
   swallows its own failures, because a watchdog that can kill the session it
   guards is worse than none. The trial is recorded rather than dropped, never
   accepted, and the session carries on — a dead executor costs one trial.

   **The new code is the first that is not VStim's, and the only one triald
   assigns to itself.** Nothing sends it. Named for what is *known* rather than
   for the timer that noticed: every existing code would have guessed.
   `CANCELLED` claims the experimenter stopped it, `NOT_STARTED` claims the
   subject did nothing, and `UNDETERMINED` is the value a trial holds *while*
   it runs — a record full of those could not be told from a session still in
   flight.

   statemachined carries the code too but may not declare it, alongside
   `UNDETERMINED`: the `.tdr` code space is one space, so leaving it out would
   mean the next outcome added there picks 11 for something else — and a
   terminal state declaring "nobody heard from me" is a contradiction.

   **The rule this leaves:** extending the taxonomy is fine; renumbering never
   is; and a new name must land in all five copies at once, because outcomes
   cross the wire *by name*.

   *(The original text, for the reasoning:)* **the one thing the broadcast model
   needs that is not built.** `Session.next_trial()` sets `_current` and nothing
   ever expires it. On the simulated path the outcome is synchronous so it
   cannot matter; on a rig it arrives from a subscription to a daemon that may
   have crashed, and nobody is responsible for delivering it — correctly so. A
   dead subscriber is therefore a session that quietly stops, with no error
   anywhere. The bound already exists on the wire: `cap_milliseconds` is what
   triald tells the executor a trial may take.

   **What is undecided is the outcome an expired trial gets.** `CANCELLED` (10)
   means "aborted by the experimenter", which is not true. `UNDETERMINED` (-1)
   is the value a trial holds *while* running and is not declarable. Neither
   says "the executor went silent", so this is a taxonomy question before it is
   a timer. Decide it before writing the timer.

## 10. Order of work

| | | Blocked on |
|---|---|---|
| 1 | ~~settle §9.5; `outcomes.json`~~ **done** — a *checker*, not a generator (§6) | — |
| 2 | ~~`trial_id` on triald's `OutcomeReport`~~ **done** | — |
| 3 | ~~The OpenAPI conformance test in statemachined (§7)~~ **done** | — |
| 4 | ~~**Stage 1 e2e**~~ **done** | — |
| 5 | ~~Answer §9.2; a `graph` on the trial type; triald's outbound client~~ **done** | — |
| 6 | ~~**Stage 2 e2e** — triald initiates~~ **done** (10 tests, [`e2e-tests/`](e2e-tests/README.md), moved here out of statemachined) | — |
| 7 | `mdns.md`; `rig=` in both daemons; vstimd's TXT records and web port | — |
| 8 | ~~§3C: vstimd's event stream; the client subscriber; triald's join~~ **done** — `triald.api.stimulus_subscriber` closes it | — |
| 9 | ~~**Stage 3 e2e**, and decide whether `rig-integration` exists~~ **done** — it does not: it is [`e2e-tests/`](e2e-tests/README.md), here | — |
| 10 | ~~**§9.7: a deadline on the trial in flight in triald**~~ **done** — `NEVER_FINISHED = 11` | — |

**Interactions A, B and C are done, and the model closes.** triald commands its
executor, subscribes to what it publishes, and now notices for itself when
nothing arrives (§9.7); it subscribes to the display the same way and owns the
join to a trial, because it is the only side that knows what a trial is. Nothing
in the loop waits on a promise nobody could keep.

**Item 7 (mDNS) is what is left, and it is independent and small** — and
vstimd's event stream needs a port advertised anyway, so a console can find it
without being told.

### Where the stage-3 tests live, and why not a fourth repo

They are in [`e2e-tests/`](e2e-tests/README.md), in this repository, and that is a change of
plan worth writing down rather than quietly doing.

The plan asked whether a `rig-integration` repo should exist. It should not,
because it would be *this* repo with a different name. The two things do the same
job at different distances: `INTERACTIONS.md` says what the daemons promise each
other and `check_outcomes.py` proves each repo's sources still say it, statically
and offline; `e2e-tests/` starts all three and watches them keep the promise. A
contract nobody checks is a wish; a suite of assertions with no written contract
is something nobody can argue with.

They also fail differently, which is the practical reason to keep both. A renamed
outcome breaks the checker in every repo in seconds, with no daemon running. A
frame counter that quietly drifted from another frame counter passes every static
check anybody could write, and only three real processes on one machine will say
so — which is exactly what `e2e-tests/` caught first (§3C).

The rule in the README still holds: nothing installs this repository. `e2e-tests/`
installs all three daemons and can only do that because nothing installs `e2e-tests/`.
It is downstream of everything and upstream of nothing.

## 11. Versioning, and what is promised from 1.0

> **Status: nothing below is promised yet.** Every daemon here is pre-1.0, and
> until each reaches it these rules describe *how* interfaces are meant to
> evolve rather than what anybody may rely on. Breaking changes are allowed now,
> and are being made — `answers` replaced a doubled `message_id` in
> mousewheeld's wire protocol the week this was written, and vstimd's
> `[[input.device.axis]] scale` changed meaning in `0.3`. **From 1.0 onward the
> rules in *The promise* are binding**, and a change that breaks one of them is
> a major version.
>
> The point of writing them down now is that they are cheap to follow while
> everything is still moving, and expensive to retrofit afterwards.

### The question is not "should we version" but "what happens when the two sides disagree"

That has three different answers on a braemons rig, and they must not share a
mechanism.

| | Who is on the other end | On a mismatch | How it is versioned |
|---|---|---|---|
| **Shared memory** (`vtl`, `vinput`) | another process on the same host, mapping the same bytes | **refuse to map** | a magic and an integer version *inside the segment*, checked at open |
| **A device wire** (statemachined ⇄ firmware, mousewheeld ⇄ firmware) | firmware that is flashed separately and will be older | refuse **below a floor**, tolerate above it | `protocol_version` in `hello`/`hello_ack`, plus additive evolution |
| **Daemon to daemon** (HTTP, ZMQ, protobuf) | another daemon, upgraded on its own schedule | **carry on** | advertised, never gated |

**Shared memory is the strict one, and it is the only strict one.** A mismatched
`#[repr(C)]` struct is not a missing field: it is bytes reinterpreted, a wheel
reading as an eye tracker with entirely plausible numbers. Both crates already
refuse to map a segment whose magic or version does not match, and that check is
what makes it safe for a producer in another repository to pin the crate at a
tag: the pin is a promise, the magic is the proof.

**A version gate between daemons is a mistake.** It converts every upgrade into
a coordinated one, on a rig whose whole arrangement (§2) is that each daemon
runs alone and commands nobody. statemachined must come up and work with no
triald on the network, and with a triald three versions newer.

### The rule: requests refuse unknown fields, responses ignore them

This is the asymmetry everything else follows from, and it is not a compromise
between strict and lax — the two directions genuinely differ.

- **A request is a command from software that believes it said something.** If
  triald sends `{zone_set, patch, origin, hysteresis_override}` and the daemon
  silently drops the field it does not know, the trial runs with a zone nobody
  asked for. Doing *part* of what was asked is worse than doing none of it, so
  an unknown field in a request is refused, by name.
- **A response, a broadcast or a record must survive a newer producer**, or
  every upgrade is a flag day. A consumer ignores what it does not recognise.

In practice: `deny_unknown_fields` on every input type, nothing of the kind on
output; proto3 on the wire, which is tolerant by construction; and an unknown
enum value decoded as its `UNSPECIFIED` variant rather than refused (vstimd's
`Enum::try_from(v).unwrap_or(Unspecified)` is the shape).

The consequence is worth stating plainly, because it is the half people forget:
**a client must not send a field the daemon does not know.** Old daemons do not
tolerate new clients' requests, deliberately. If a client needs a field that may
not be there, it asks what it is talking to first.

### So are we forward compatible?

Not yet, and the words are worth separating.

| | Means | State |
|---|---|---|
| **Backward compatible** | new code reads old data; a new daemon serves an old client | mostly, by additive habit; not promised |
| **Forward compatible** | old code tolerates new data; an old consumer survives a newer producer | **for responses, broadcasts and records** — this is what proto3 and ignore-unknown buy |
| Forward compatible *requests* | an old daemon tolerates a newer client's command | **no, and deliberately never** — see the rule above |

### The promise, from 1.0

Within a major version, for every interface in §3 and the fast bus beside it:

1. **Additive only.** Fields, message types, routes and enum values may be
   added. Nothing is removed, renamed, renumbered or given a new meaning.
   A protobuf field number is never reused, ever, including across majors.
2. **Responses, broadcasts and records stay readable by older consumers.** A
   consumer that ignores what it does not recognise keeps working.
3. **Requests keep refusing unknown fields.** A client that needs a new field
   checks the version first; that is what the version advertisement is for.
4. **A shared-memory layout change bumps the version in the segment** and is a
   coordinated upgrade of both packages by definition — the reader refuses the
   writer until both are replaced. It is therefore a major event even when the
   daemon's own version is not.
5. **A device wire keeps a floor**: a daemon states the oldest `protocol_version`
   it speaks and refuses below it, by name, rather than talking to firmware it
   half-understands.
6. **Defaults are part of the interface.** A field whose default changes is a
   field whose meaning changed.

### Where a version is stated

A console showing three daemons' panels needs to know what it is showing, and
the daemons cannot agree on a transport: statemachined, triald and mousewheeld
speak HTTP+JSON, and vstimd's control surface is protobuf over a WebSocket. So
the uniform place is the one they already share — **the mDNS TXT record**, which
carries `id`, `version`, `api`, `elements`, `device` and `port` today.

- `version` — the daemon's own release.
- `api` — the interface contract's major version, which is what a client cares
  about and is not the same number.
- `proto` — for a daemon that owns firmware, the device `protocol_version` range
  it speaks.

Each daemon also answers in its own surface, in its own shape: vstimd in
`QueryServerInfo`, the others on a route. **Do not force a common transport for
this**; forcing a REST endpoint onto vstimd to satisfy a table would be the
wrong end of the stick.

### What is not versioned

**The shared vocabulary of §6.** The `.tdr` outcome taxonomy lives in five
copies across two repositories, one of them firmware, and they must be
*identical* — a version number would only make being legitimately out of step
expressible. The check for that is `check_outcomes.py` in CI: a test, not a
number on a wire.
