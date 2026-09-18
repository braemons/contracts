# What a braemons daemon looks like

Four repositories hold a daemon — vstimd, statemachined, triald, mousewheeld —
and until now each was laid out however it grew. This document is the shape they
all take, and the rule about where a daemon's public interface is written down.

It is binding on those four. `console` is not a daemon and `contracts` runs
nothing, so neither is bound by it.

Two claims, and the second is the one that matters:

1. **Every daemon has the same directory layout**, so that knowing one repository
   is knowing all of them.
2. **A daemon's public interface is authored in protobuf** — its types *and* its
   behaviours — and nothing else in the repository is allowed to be the authority
   on what that interface is.

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
`packaging/Makefile` and `api/app.py` are the reference.

`daemon/` also needs its own `README.md` and `LICENSE`: PEP 621 metadata may not
point above the project directory, and a symlink does not survive the sdist
either. The package README is a different document from the repository's — one
is read after `pip install`, the other on GitHub — so this is not a copy. The
licence text is.

**`client/` is a sibling of `daemon/`, never a subdirectory of it.** A person who
wants to talk to a rig should not have to install the thing that runs one.
statemachined is the cautionary case: its client lives inside the daemon package
today, so `pip install` for a one-line script pulls in a serial driver, a graph
store and a web server.

## 2. The interface is proto

### Types and behaviours, both

A `.proto` file carries the messages **and** the `service` blocks. An rpc is how
a behaviour gets written down:

```proto
service Zones {
  // Resolve the patch against the calibration, compile, upload, and wait for
  // the board to answer `armed`. Refuses if the set does not fit.
  rpc Arm(ArmRequest) returns (ArmedZones) {
    option (braemons.v1.route) = { method: "POST", path: "/api/zones/arm", body: "*" };
  }
}
```

Without the service block a `.proto` is a bag of structs and the *behaviour* —
what you may ask for, in what order, and what comes back — lives in six handler
files again. The rpc is the part that makes the file readable as an API.

### It is an IDL, not a transport

**No gRPC.** `INTERACTIONS.md` §7 settles this and the reasoning stands: the IDL
is what earns its keep, and the RPC framework is what takes away `curl` and
MATLAB's `webread`. `braemons.v1.route` binds each rpc to the HTTP route it
actually rides, and that route is the only way to call it.

That option is ours, in `proto/braemons/v1/route.proto`, identical in every
repository and vendored from here. It is deliberately not `google.api.http`,
which exists to feed grpc-gateway and the googleapis OpenAPI generators —
neither of which this family uses, so vendoring somebody else's schema to borrow
their recognition would be paying for a tool we decided against. It also carries
one thing theirs does not: `websocket: true`, which is how a stream gets
declared at all. OpenAPI had no vocabulary for one, which is why three of this
family's four streams were specified only in prose.

vstimd has done exactly this since before it was a policy — `service.proto`'s
service block is marked *"for future gRPC transport"* and is unused.

### JSON is the wire, and proto owns it

Every HTTP and WebSocket byte in this family is JSON, because the console
panels are served as written — no build step, no framework, no CDN — and a
browser has no protobuf decoder without one of those. `curl` and MATLAB want the
same thing.

So **protobuf's JSON mapping is the serialisation format** and the binary
encoding goes unused. This is not a detail. It means the mapping's habits are
what every panel and every `curl` sees, and they have to be handled rather than
tolerated:

| | what it does | what we do |
|---|---|---|
| camelCase | `position_cm` → `positionCm` | `json_name` on every field whose name is more than one word |
| int64 | `41822` → `"41822"` | accepted; clients parse. A counts field that is not an integer misleads every reader of the proto |
| enums | `DISPLACEMENT` or `ZONE_METRIC_DISPLACEMENT`, depending on the generator | **keep the prefix**, on both sides of every generator |
| a field at its default | omitted entirely | **emit it**: a wheel at rest must not answer without a `position_cm` |
| unknown fields | refused or ignored, depending on the generator | refuse them, which is §11's rule for a request and pbjson's default |

The last three are settings, not laws, and the settings are per generator — which
is the trap. `pbjson` strips an enum's prefix by default and Python's
`json_format` does not, so two clients generated from one `.proto` disagree
about the same byte. A daemon's generator options are part of its interface and
belong in a test that prints the bytes, not in somebody's memory.

mousewheeld's `daemon/tests/wire_json.rs` is that test: eight cases, every
assertion written by printing the JSON first.

If the daemon's own serde or pydantic types keep producing the bytes and the
proto merely describes them, this whole arrangement is OpenAPI again with a
different syntax. **The generated code must be what serialises**, or the file is
an afterthought and will drift.

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
  maps, scene-configs, policies, `outcomes.json`. These are documents somebody
  writes and reviews in a diff; they keep serde/pydantic and their own JSON
  Schema. mousewheeld's committed `zone-set.schema.json` stays exactly as it is.
- **The fast bus.** `vinput` and `vtl` are a seqlock over a C layout, not a
  serialisation format.
- **Rig configs.** TOML, and they stay TOML.
- **The device wire, for now.** NDJSON + CRC-16 to the board. §6 explains why the
  firmware cannot consume protobuf today; nanopb makes it possible and is
  planned, starting with mousewheeld's board because it is the only one not yet
  written. Framing and CRC survive either way — protobuf is not self-delimiting.

## 3. Where the proto lives — and why not one repo

Each daemon's `proto/` is in its own repository, and `contracts/vendored/proto/`
holds a copy of all four for reading side by side.

This is the `outcomes.json` arrangement, for the same reason: a single shared
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
| `make check-proto` | the `.proto` compiles, the committed generated code is what it produces, and every served route has an rpc above it |
| `make dev` | against a simulator, elements served from disk |
| `make docs` `make package` | |

Generated code is committed rather than built, so that a checkout builds without
`protoc`, a reviewer sees the interface change in a diff, and anybody reading the
repository — including an AI — can read the types instead of inferring them from
a build directory.

`check-proto` is what makes "never out of date" mechanical, and it is a copy of a
pattern already proven here: mousewheeld's `check-schema` regenerates and runs
`git diff --quiet`.

The generated types keep the *shapes* honest on their own — a field renamed in
the proto stops the daemon compiling. What nothing catches without help is a
**route**: a handler wired into the router with no rpc above it is public API
that is written down nowhere, and an rpc whose route was never wired is a
promise to a client that 404s. So `check-proto` reads the router and the service
blocks and holds them to each other. mousewheeld's `tools/check_routes.py` is
the reference implementation, and it reads the `.proto` as text on purpose: a
custom option is only legible in a descriptor set with the protobuf runtime to
hand, and this has to run in CI on a machine with nothing installed but
`python3` — the same reason `check_outcomes.py` is regexes over source files.

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
  consumer, not the family — is unchanged. Control planes are HTTP+JSON;
  high-rate streams are protobuf over ZMQ; the fast bus is shared memory.
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

mousewheeld goes first because it is the newest, has no client to break, and is
in the same language as vstimd, whose prost and pbjson toolchain is already
proven against a real wire.
