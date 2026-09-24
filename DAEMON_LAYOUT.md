# What a braemons daemon looks like

Four repositories hold a daemon — vstimd, statemachined, triald, mousewheeld —
and until now each was laid out however it grew. This document is the shape they
all take, and the rule about where a daemon's public interface is written down.

It is binding on those four. `console` is not a daemon and `contracts` runs
nothing, so neither is bound by it.

Four claims, and the last three are the ones that matter:

1. **Every daemon has the same directory layout**, so that knowing one repository
   is knowing all of them.
2. **A daemon's public interface is authored in protobuf** — its types *and* its
   behaviours — and nothing else in the repository is allowed to be the authority
   on what that interface is.
3. **Every wire in the family carries protobuf**, and every daemon follows it:
   the control planes, the event streams, the browser edges, and the links to
   the boards. **This transition is not complete** — §2.1 says where each daemon
   stands and what is left.
4. **Every daemon ships the same way**: at least a **Python client**
   (`client/python/`, generated from its `proto/`), a **systemd unit**, and at
   least a **`.deb`** package. A rig installs a daemon with the package manager,
   runs it as a service, and scripts it from Python — whichever daemon it is.

## 1. The layout

```
<daemon>/
├── proto/<daemon>/v1/*.proto    THE interface: types + service/rpc.
│                                Hand-authored, reviewed by a person, never generated.
├── daemon/                      the service itself
│   ├── Cargo.toml | pyproject.toml
│   └── src/{api,wire,convert,model,…}
├── client/
│   ├── python/                  public dataclasses; the generated code is private
│   └── web/elements/            custom elements, the /elements/ contract
├── firmware/                    where a board exists
├── docs/reference/api.md        the document a person reads
├── dev/PLAN.md                  design notes; not published
├── packaging/                   nfpm, systemd, udev, sysusers, the rig config
├── Makefile
├── README.md
└── LICENSE
```

The top level is language-neutral and the language appears one level down, in
`daemon/Cargo.toml` or `daemon/pyproject.toml`. `daemon/` rather than `server/`
or `python/` or `src/`, because the family calls them daemons in every other
sentence it writes.

**A Python daemon has one wrinkle, and it is worth writing down because the
obvious answer is wrong.** Rust embeds `../client/web` at compile time and that
is the end of it; Python ships data *inside* a package, so the panels have to be
copied in somewhere. hatchling will force-include a path from outside the
project and it looks like the answer — but `uv build` builds the wheel from the
sdist, an sdist cannot contain a path above its own root, and the wheel then
fails on a machine that never saw the repository. The copy belongs in the
packaging step, which is already copying a staged tree, and the daemon falls
back to the authored location when no copy has been made — which is every
editable install, and therefore every developer. triald's
`packaging/Makefile` and `find_web_root` in `api/web_edge.py` are the
reference, and its `check-staged-tree.py` is the other half: the copy drops the
panels' build inputs by name, and the check asserts from the other side that
everything left under `web/` is something a browser would ask for.

`daemon/` also needs its own `README.md` and `LICENSE`: PEP 621 metadata may not
point above the project directory, and a symlink does not survive the sdist
either. The package README is a different document from the repository's — one
is read after `pip install`, the other on GitHub — so this is not a copy. The
licence text is.

**`client/web/` has a build step, and its output is committed.** The API is
gRPC, and a browser cannot make a protobuf client out of nothing: it needs the
generated types and a gRPC-Web transport, bundled. That is one build step and it
is allowed — but it runs *before* the daemon builds, never during. A Rust daemon
embeds `client/web/` at compile time, so a bundle produced by `cargo build`
would make npm a build dependency of the daemon on every rig and in every
release; a Python daemon has the same problem one step later, at `uv build`.

So: `client/web/package.json` with a lockfile, one script, `node_modules/` and
any intermediate generation ignored, and the bundle committed beside the
hand-written panels with a `@generated` line at the top of it. `make web`
regenerates; `make check-web` regenerates and fails if the committed bundle is
not what `proto/` produces — the same arrangement the generated server code is
already under, for the same reason. Only the client is bundled: the panels stay
plain ES modules served as written, so editing one and reloading the page still
works. mousewheeld's `client/web/build_daemon_api_client.mjs` is the reference.

**`client/` is a sibling of `daemon/`, never a subdirectory of it.** A person who
wants to talk to a rig should not have to install the thing that runs one.
statemachined is the cautionary case: its client lives inside the daemon package
today, so `pip install` for a one-line script pulls in a serial driver, a graph
store and a web server.

### Names are long, and say what the thing is

A name is read far more often than it is typed, and the reader is usually
somebody who has not seen this file before. So: **a class or a file is named
for what it is, in full, and an abbreviation is not a name.**
`SerialMonitorPanelElement`, `file_schema_types.rs`, `daemon_refusals.py`,
`build_daemon_api_client.mjs`, `DaemonRefusedTheRequest`.

Three rules that follow, and each was learned by getting it wrong:

* **A client is `<Daemon>Client`** — `MousewheeldClient`, and `TrialdClient`
  and `StatemachinedClient` when they come. Not `Rig`: a rig has four daemons
  on it, and a script that talks to two of them has to be able to say which is
  which. Not `Client` either, for the same reason at a call site.
* **A type that will be imported into somebody else's namespace carries its
  subject.** `ZoneBoundReference`, not `Reference`; `BoardCapacities`, not
  `Capacities`. `from mousewheeld import Reference` is a name collision waiting
  for the second import line.
* **A file is named like a class, not like a folder.** `client.py`, `types.py`
  and `cli.py` say only where they sit in a convention; `daemon_client.py`,
  `api_types.py` and `command_line_interface.py` say what is in them. The few
  fixed names a tool insists on — `__init__.py`, `conftest.py`, `mod.rs` — are
  the exceptions, and they are exceptions because they are addresses rather
  than descriptions.

The same rule made `Rig` into `DaemonServices` on the Rust side: it is not a
rig, it is this daemon's implementation of every service the proto declares.

### Two configurations, and they are never the same file

Every daemon in this family has exactly two kinds of configuration, and they
are unrelated to each other. vstimd has had the distinction written down
longest; it is the family's, not vstimd's.

| | **the rig config** | **the experiment's documents** |
|---|---|---|
| what it describes | this box: the device, the display, the ports, the directories | one experiment: what will actually be run |
| format | TOML | JSON |
| where | `/etc/braemons/<daemon>-rig-config.toml` | under `/var/lib/braemons/<daemon>/` |
| who writes it | whoever set the hardware up, once | whoever is running the study, per session |
| how often it changes | when the hardware does | between sessions, sometimes between blocks |
| in the package | a conffile: shipped, and never overwritten on upgrade | nothing — a fresh install has none |
| over the API | read, and a handful of fields patchable **until restart** — never written back to `/etc` | the store rpcs, as text |

**Both are documents, so neither is protobuf.** That is §2's rule below, and
this is the largest thing it covers: a rig config and a scene config are files
somebody edits and reviews in a diff, they keep serde or pydantic and their own
JSON Schema, and a `.proto` describing them would be a second description that
loses the day it disagrees.

What each daemon calls its experiment documents follows the work rather than a
template — a scene is not a zone set is not a graph — but the shape is the
same: named files, in a store, under the storage directory, reachable by name
and never by path.

| | rig config | experiment documents |
|---|---|---|
| **vstimd** | `vstimd-rig-config.toml` — VTL shm, display mode, thread scheduling | scene configs: `projects/<project>/scene-configs/<name>.config.json` |
| **mousewheeld** | `mousewheeld-rig-config.toml` — the board, the rates, the shm segment | zone sets: `zone-sets/<name>.json` |
| **triald** | `triald-rig-config.toml` — the port, the directories, the executor, and `session_config` naming the next column | the session config: the declarative settings and the trial type sets, in one file. Uploaded policies sit beside it |
| **statemachined** | `statemachined-rig-config.toml` — the device target, the ring, the directories | state-machine configs and graphs |

**Never call either of them `config` on its own.** The word alone is ambiguous
in every one of these repositories, and the two things it could mean are the
two least alike: a file about the hardware that changes once a year, and a file
about an experiment that changes between blocks. A flag, a field, a class or a
CLI argument says which — `--rig-config`, `--session-config`, `scene_config`,
`state_machine_config`. vstimd's CLAUDE.md has enforced this for longer than
this document has existed.

**Addressed by name, never by path.** A daemon takes one storage directory and
owns the tree under it; a client asks for `corridor`, not
`/var/lib/braemons/mousewheeld/zone-sets/corridor.json`. A path in a request is
a path that means something different on the next rig, and it is also how an
API becomes a way to read `/etc/shadow`.

**A rig config is never written back from the API.** mousewheeld patches
`rate_hz` and two neighbours, statemachined patches the device target; both
last until the daemon restarts and neither touches `/etc/braemons`. That file
belongs to whoever set the box up: an API that rewrote it would make the
running daemon the authority on what the hardware is, and would silently
diverge from the conffile the next upgrade compares against.

#### What does not match this yet

Writing it down found four things, three of them the kind this rule exists to
prevent:

* **statemachined's rig config flag is `--config`.** The other three are
  `--rig-config`, and bare `config` is the one spelling this rule forbids.
* **triald's `--config` is the *session* config.** So the same flag name means
  the box on one daemon and the experiment on another, which is exactly the
  confusion the rule is about. It wants to be `--session-config`.
* **mousewheeld's storage directory defaults to `/var/lib/mousewheeld`**, not
  `/var/lib/braemons/mousewheeld`. triald's unit file already carries the
  reason in a comment: every braemons daemon keeps its state under one parent,
  so a rig has one directory to back up.
* **triald has the flag and not the file.** `--config` and the rig config's
  `session_config` both exist; `_load_config` raises "not implemented yet", the
  daemon runs a built-in demo experiment, and a set written over the API lives
  in memory and does not survive a restart. This is the one gap that is a
  missing *thing* rather than a misspelt name, and a daemon whose experiment
  cannot be written down is a daemon whose sessions cannot be reproduced.

None of the three names is load-bearing anywhere yet — nothing is shipped or
used (§5) — so each is a rename rather than a migration. The fourth is work.

## 2.1 Protobuf on every wire — the rule, and where the family stands

**The protocol is protobuf everywhere.** Not only where a daemon's public API is
written down, but on every wire a daemon speaks:

- **Control planes** — gRPC, `proto/<daemon>/v1/`. Rust daemons serve the
  browser on the same port through `tonic-web`.
- **Event streams** — protobuf, over whichever transport the consumer needs.
  Some daemons **publish events over ZeroMQ**, each message a protobuf from the
  daemon's own `proto/`: vstimd's frame and stimulus events on its PUB socket,
  which triald subscribes to. Others stream them as gRPC server streams
  (statemachined's `WatchTrace`, mousewheeld's `WatchState`). Either is within
  the rule; JSON on a PUB socket would not be.
- **Board links** — protobuf too, generated for the firmware with **nanopb**,
  in COBS frames with a CRC-16:
  `COBS(protobuf ‖ CRC-16, big-endian) ‖ 0x00`. The schema is the daemon's own
  `proto/<daemon>/link/v1/link.proto`, a separate package from its API so the
  two never share a message by accident. mousewheeld's link is the reference;
  statemachined's follows it.

What is **not** a wire and stays as it is: documents on disk (graphs, zone sets,
state-machine configs, line maps, scene-configs) keep serde/pydantic and JSON
Schema, because a person edits them; rig configs stay TOML; the fast bus is
shared memory. See "What proto does not touch" below.

**No new JSON on a wire, in any repository.** A daemon that still speaks JSON
somewhere is carrying a debt on this list, not a design choice, and a change
that adds one is refused in review.

### What every daemon has

Both rules — protobuf on every wire, and claim 4's shipping rule — hold for all
four as of the 0.3 alphas:

| daemon | Python client | systemd unit | `.deb` |
|---|---|---|---|
| **vstimd** | `client/python/` (`vstimd-client`) | `packaging/systemd/vstimd.service` | cargo-deb |
| **mousewheeld** | `client/python/` | `packaging/mousewheeld.service` | nfpm |
| **statemachined** | `client/python/` (`statemachined-client`) | `packaging/systemd/statemachined.service` | nfpm |
| **triald** | `client/python/` | `packaging/systemd/triald.service` | nfpm |

A new daemon joins the family with all three, and a daemon that drops one is
out of line with this document.

| daemon | control plane | events | browser | board link | release |
|---|---|---|---|---|---|
| **vstimd** | protobuf over ZMQ | protobuf over ZMQ | protobuf over a WebSocket | — | `v0.3.0-alpha2`; ZMQ is its transport by design (§6) |
| **mousewheeld** | gRPC | gRPC streams | gRPC-Web (`tonic-web`) | protobuf + nanopb | `v0.3.0-alpha1` |
| **statemachined** | gRPC | gRPC streams | gRPC-Web (`tonic-web`) | protobuf + nanopb | `v0.3.0-alpha1`, the Rust daemon |
| **triald** | gRPC | gRPC streams | gRPC-Web, binary, on a second port (Python) | — | `v0.3.0-alpha2` |

The e2e suite (`e2e-tests/`) pins these releases and its `make test` is green
on them. A daemon that takes JSON onto a wire again is out of line with this
section, and so is a new one that arrives without all three of the shipping
table's columns.

## 2. The interface is proto

### Types and behaviours, both

A `.proto` file carries the messages **and** the `service` blocks. An rpc is how
a behaviour gets written down:

```proto
service Zones {
  // Resolve the patch against the calibration, compile, upload, and wait for
  // the board to answer `armed`. Refuses if the set does not fit.
  rpc Arm(ArmRequest) returns (ArmedZones);
}
```

Reached at `POST /mousewheeld.v1.Zones/Arm`, which gRPC decides and nobody
writes down.

Without the service block a `.proto` is a bag of structs and the *behaviour* —
what you may ask for, in what order, and what comes back — lives in six handler
files again. The rpc is the part that makes the file readable as an API.

### It is an IDL **and**, on a control plane, the transport

**gRPC for control planes** — statemachined, triald and mousewheeld.
`INTERACTIONS.md` §7 records how that answer changed and what it was measured
against; the short version is that these daemons do not do CRUD, every objection
to gRPC rested on a premise that has since moved, and the family had begun
reimplementing gRPC one piece at a time.

**vstimd stays ZMQ**, because it is time-critical and its clients decode a
high-rate stream — protobuf over ZMQ, which satisfies §2.1. The fast bus —
`vinput`, `vtl` — is shared memory and is not an RPC at all. The device wire
is protobuf too, through nanopb (§2.1), which is not gRPC: a board speaks
framed messages, not rpcs.

`braemons.v1.route` is therefore **gone**. An rpc's route is
`/<package>.<Service>/<Method>`, decided by gRPC, and the generated service
trait makes an unimplemented rpc a compile error — which is the job a route
checker was doing by reading source code.

vstimd has done exactly this since before it was a policy — `service.proto`'s
service block is marked *"for future gRPC transport"* and is unused.

### The wire is binary, and the JSON mapping is gone

protobuf's own encoding, over HTTP/2. The whole table of JSON-mapping habits
that used to live here — camelCase, `int64` as a string, enum prefixes, whether
a field at its default is emitted — is **deleted**, because none of it happens
any more. Those were five settings that differed per generator, so two clients
built from one `.proto` could disagree about the same byte; each needed a test
that printed the bytes to pin it. The binary encoding has no such freedom.

`json_name` annotations stay in the `.proto` regardless. They cost nothing, and
anything that does render one of these messages as JSON — a log line, a debug
dump, a recorded trial — should spell `position_cm` rather than `positionCm`,
because that is the family's rule that a quantity names its unit.

**What is lost is `curl`.** That was the last objection standing and it was
traded deliberately: every daemon ships a client library and a CLI, and those
are the supported way in. A rig at three in the morning is reached with the
daemon's own CLI rather than by hand-writing a request.

**A browser reaches a daemon through gRPC-Web**, translated in process by
`tonic-web` — no proxy, no second daemon, no second port. The panels get a
generated client and therefore a build step, which is a change from the
`/elements/` contract's original *no build step* and is allowed: that rule
existed so a **console** would not need one, and a client the daemon serves
still satisfies it.

### A Python daemon binds twice, and the ports are allocated with that in mind

`tonic-web` is the Rust half of this. A **Python** daemon has no equivalent:
`grpc.aio` owns its port outright and no ASGI server speaks native gRPC, so it
cannot serve gRPC and a browser on one socket. It binds two — the panels on
`--port` and gRPC on `--port + 1` — and the browser reaches it over
**gRPC-Web**, the same transport `tonic-web` gives the Rust daemons' panels,
answered by a small ASGI edge written against the specification (triald's
`api/web_edge.py`) that dispatches into the same servicers. Binary only: the
trailers travel as a frame of the body, so it needs neither HTTP trailers nor
HTTP/2, and there is no JSON codec to fall back to. (triald spoke Connect until
0.3, and its panels sent Connect's default, JSON.)

`+ 1` is derived and not a second setting, because a second setting is one
nobody remembers to change. The cost is that a Python daemon occupies a *pair*,
and the family's ports were allocated one per daemon before any of them did:

| daemon | panels | gRPC | ports |
|---|---|---|---|
| **vstimd** | 8080 | same (`tonic-web`) | one |
| **statemachined** | 8081 | 8082 | **two** |
| **mousewheeld** | 8083 | same (`tonic-web`) | one |
| **triald** | 8420 | 8421 | **two** |

mousewheeld was 8082 and moved, because statemachined's derived port landed on
it and a rig running both on their defaults collided. It moved rather than
statemachined because it is the one that needs a single port, and because 8081
is what a console's `rigs.json` and every packaged unit file already hold.

**The rule this leaves**: a Python daemon's port and the one above it are both
spoken for, so no two daemons may be given adjacent numbers unless both are
Rust. vstimd and statemachined are adjacent and that is safe only for as long
as vstimd stays Rust; if it ever needs two, it moves, not statemachined.

### The three layers, and the rule

```
proto/<daemon>/v1/*.proto     hand-authored. THE interface.
   ↓ make proto — output committed
daemon/src/wire/              generated. Rust: pub(crate). Python: _proto/.
   ↓ daemon/src/convert/      exhaustive, hand-written, X_from_wire / X_to_wire
daemon/src/model/             serde or pydantic. File shapes, unions, validators,
                              runtime state. Knows nothing about protobuf.
```

**A generated type never appears in a public signature** — not in the Python
client's, not in a return type anybody outside `api/` can name. vstimd already
works this way: `client/python/vstimd/_proto/` is private and
`system/system_models.py` holds the dataclasses people actually touch.

The seam is not ceremony. It is what lets a file on disk and a message on a wire
have different shapes when they should: mousewheeld's zone bound is `"$goal_cm"`
in a file a person edits, and would be `{"reference": "goal_cm"}` if protobuf
described it. The file wins, because a person types it.

### What proto does not touch

- **Files on disk.** Zone sets, graph definitions, state-machine configs, line
  maps, scene-configs, policies. These are documents somebody
  writes and reviews in a diff; they keep serde/pydantic and their own JSON
  Schema. mousewheeld's committed `zone-set.schema.json` stays exactly as it is.
- **The fast bus.** `vinput` and `vtl` are a seqlock over a C layout, not a
  serialisation format.
- **Rig configs.** TOML, and they stay TOML.
- **The device wire's framing.** The board link carries protobuf (§2.1), but
  it is not gRPC and not the API package: its messages are the daemon's
  `link/v1/`, and it keeps its own framing and CRC, because protobuf is not
  self-delimiting. What NDJSON + CRC-16 used to be here is COBS + CRC-16 now,
  wherever the transition is done.

### One package per daemon, and one for the family

A daemon's proto package is its own name: `triald.v1`, `mousewheeld.v1`,
`statemachined.v1`, `vstimd.v1`. **There is no family prefix on them**, and
that is a decision rather than an omission. The package is half of every
address a person types — `grpcurl … triald.v1.Trial/ReportOutcome` — and a
`braemons.` in front of it would be earned by a name collision that cannot
happen on a rig with one of each daemon. Uniqueness against the world is what
the style guides are protecting, and nothing here is published to a registry
the world shares.

`braemons.v1` exists for the other case: **a type two daemons must agree on and
neither is the authority for.** The bar is both halves. Today it holds exactly
one thing, the `.tdr` outcome taxonomy — statemachined reports an outcome,
triald records one — which lived in `triald.v1` until the day it was vendored
into statemachined, where `package triald.v1` would have put a `triald` module
inside statemachined's generated tree, on rigs where the real one is installed.

A type only *one* daemon owns stays in that daemon's package even when others
read it. Vendoring a neighbour's proto to read it is normal; moving it to
`braemons.v1` because two repositories touch it is how a shared package becomes
a junk drawer.

**A Python daemon rewrites protoc's imports.** protoc roots a generated
module's imports at the proto path, so the stubs reach each other as `from
triald.v1 import …` — which is the daemon's own package name, and which no
`__path__` trick can resolve for a second package like `braemons.v1`. One sed
in `make proto` rewrites them to absolute paths inside `_proto/`, and
`check-proto` compares the rewritten output. The `__path__` arrangement vstimd's
client still uses works, but nothing static can follow it, so a type checker
silently skips every module that imports through it — including the convert
seam, which is the one worth checking.

## 3. Where the proto lives — and why not one repo

Each daemon's `proto/` is in its own repository, and `contracts/vendored/proto/`
holds a copy of all four for reading side by side — plus `braemons/v1/`, which
is canonical here because it belongs to no one daemon.

This is the arrangement `outcomes.json` had before it became a proto, for the
same reason: a single shared
proto repository is a build dependency in every daemon, and "small and optional
interfaces" does not survive one. §7 records that argument in full. Vendoring
costs exactly one thing — nothing makes the copies match — and
`check_vendored_copies.py --fix` is that one thing.

## 4. The uniform Makefile

Same target names in all four repositories:

| target | |
|---|---|
| `make proto` | regenerate from `proto/`; the output is **committed** |
| `make build` `make test` | |
| `make check` | build + lint + test + `check-proto` + `check-text` |
| `make check-proto` | the `.proto` compiles and the committed generated code is what it produces |
| `make dev` | against a simulator, elements served from disk |
| `make docs` `make package` | |

Generated code is committed rather than built, so that a checkout builds without
`protoc`, a reviewer sees the interface change in a diff, and anybody reading the
repository — including an AI — can read the types instead of inferring them from
a build directory.

`check-proto` is what makes "never out of date" mechanical, and it is a copy of a
pattern already proven here: mousewheeld's `check-schema` regenerates and runs
`git diff --quiet`.

**There is no route checker any more.** There was one, in two repositories,
holding a hand-maintained router to the rpcs in the `.proto` — a handler wired
up with no rpc above it was public API written down nowhere. Under gRPC the
generated service trait has a method per rpc and the compiler refuses an
incomplete implementation, so the check is the type system's.

`buf lint` and `buf breaking` run in CI. `buf breaking` is what turns §11's
additive-only promise from a paragraph into something that fails a build.

## 5. What each repository moves

| | today | move |
|---|---|---|
| **vstimd** | `server/`, `client/{python,web}`, `proto/` | `server/` → `daemon/`. Nothing else; it is the reference. |
| **mousewheeld** | `daemon/`, `web/`, `rotary-encoder/` | `web/` → `client/web`; `rotary-encoder/` → `firmware/`; add `proto/`, `client/python/`, and a LICENSE — it has none |
| **statemachined** | `python/` (daemon, client, device and model fused), `firmware/` | split `python/` → `daemon/` + `client/python/`; `daemon/web/elements` → `client/web`; add `proto/` |
| **triald** | `src/`, `tests/` | `src/` → `daemon/src`; `src/triald/web` → `client/web`; add `proto/`, `client/python/`; `dev/API.md` → `docs/reference/api.md` |

Nothing in this family is shipped or used anywhere yet, so all of this is a
rename rather than a migration. There is no compatibility window to keep and no
deprecation to stage.

## 6. What is deliberately not uniform

- **Language.** Two daemons are Rust and two are Python, and that follows the
  work: a renderer and a serial link are not a trial policy.
- **Firmware.** Only statemachined and mousewheeld have a board.
- **Transport beyond the control plane.** §7's rule — the transport follows the
  consumer, not the family — is unchanged, and the *encoding* is not optional:
  control planes are gRPC; high-rate streams are protobuf over ZMQ; board links
  are protobuf through nanopb; the fast bus is shared memory. See §2.1.
- **What is in `docs/`.** The layout fixes `docs/reference/api.md`; everything
  else in there is each repository's business.

## 7. Open decisions

- **statemachined's `device/`** is a direct serial driver its own docstring calls
  *"one of the two ways to drive a rig from Python"* — a public library that is
  not a daemon client, sharing `model/` and `graph_set_compiler.py` with the
  daemon. It goes into `client/python` as the direct-attach path, or into a
  `lib/python/` of its own. **Undecided.**
- **Whether the served OpenAPI document survives.** The proposal is that it does
  not: serve a `FileDescriptorSet` at `/api/descriptor.pb` for generators and
  keep the hand-written `docs/reference/api.md` for people. This supersedes §7's
  closing suggestion to commit the generated OpenAPI document — that was the
  right answer while the schemas could only be *generated from* FastAPI, and the
  point of this document is that they no longer are.

## 8. Order of work

1. The directory moves, all four repositories — mechanical, no behaviour change.
2. Toolchain: `buf` per repository, committed codegen, `check-proto`,
   `contracts/vendored/proto/`.
3. **mousewheeld** — the HTTP surface in proto, `wire/` and `convert/`, and the
   first generated Python client.
4. **triald** — 33 rpcs; `api/schemas.py` is already a seam.
5. **statemachined** — 46 rpcs, and `model/` has to be teased apart into wire,
   file and runtime before a wire type can exist.
6. Firmware, with nanopb: mousewheeld's board first, then statemachined's.
   **Both written, neither merged to `main` yet** — see §2.1.

mousewheeld goes first because it is the newest, has no client to break, and is
in the same language as vstimd, whose prost and pbjson toolchain is already
proven against a real wire.
