# The Lab Streaming Layer, in addition

> **Status: decided in principle, not designed.** braemons keeps its own
> streams and adds LSL beside them (INTERACTIONS.md §9.8). This is what LSL
> would add, what it would not replace, and what has to be settled first.

## Why this came up

Precise times on a braemons rig are the acquisition system's: it records every
TTL the boards emit — statemachined's lines, vstimd's VTL through daqd,
lickd's lick and rate lines. triald also collects the daemons' events and
records them, at lower precision, and that record has to be **joinable** — to
the acquisition record, and across daemons. Today each daemon stamps with its
own clock:

| daemon | stamps its events with |
|---|---|
| vstimd | `monotonic_us` since its own process started, and the display's frame index |
| lickd | `CLOCK_MONOTONIC` ns on its host, and the board's µs |
| statemachined | its board's µs within a trial (host stamps to be surveyed) |

`CLOCK_MONOTONIC` would do on one host; a rig is not always one host, and a
process-relative clock never does. Counting TTL edges joins a daemon to the
acquisition record with no clock (lickd's `licks_total`, INTERACTIONS.md §3
G–I), but only for events that have a line.

## What LSL does

[Time synchronisation](https://labstreaminglayer.readthedocs.io/info/time_synchronization.html):

- Every outlet stamps samples with its own local monotonic clock
  (`std::chrono::steady_clock`) and never adjusts it.
- A consumer measures each outlet's offset NTP-style: eight UDP round trips,
  keeping the offset from the one with the smallest round-trip time, repeated
  every few seconds.
- The recorder (LabRecorder) writes timestamps and every offset measurement
  unmodified into XDF. The importers (pyxdf, xdf-Matlab) fit a line to each
  stream's offsets — drift assumed linear — remap its timestamps, and dejitter
  regularly sampled streams from their sample indices.
- Online correction is opt-in (`proc_clocksync`, `proc_ALL`), and its smoothing
  needs up to five minutes to settle.
- Accuracy: "reasonably well below a millisecond" on a LAN, biased by half any
  asymmetry in the network path; the OS's own timestamp jitter is at least ten
  times larger than the synchronisation's.

That is §2's rule applied to time: a participant knows nobody and states its
own clock, and the consumer that records does the measuring.

## Why in addition, and not instead

**What braemons' own streams have that LSL does not:**

- **Typed messages.** `lickd.v1.Event`, `vstimd.v1.Event` are protobuf with a
  contract per daemon, checked in CI. An LSL marker stream is strings or
  numbers per sample.
- **Loss that is visible.** Per-topic sequence numbers and `loss.detected`
  name exactly what a subscriber missed. That is what lets triald decide what
  a hole means for a trial.
- **The join to a trial.** triald's record is built on these streams, and
  every rule in INTERACTIONS.md is written against them.
- **No multicast.** Discovery is mDNS and fixed ports on an isolated rig
  network.

**What LSL adds that braemons does not have:**

- **A recorded clock model across hosts**, done offline, with every
  measurement kept.
- **One file with everything else in the lab.** EEG, ephys, eye trackers and
  cameras already publish LSL. A rig's licks, rates and wheel position as LSL
  outlets land in the same XDF with no converter.
- **Analog data as a first-class stream.** Regular-rate sampled streams, with
  dejittering, are what LSL does best.

So: keep the braemons streams as the contract, and add LSL outlets beside them.

## What to settle

1. **Where the outlet lives.** In each daemon, through `liblsl` (the Rust
   bindings wrap the C library), or **one relay per rig** that subscribes to
   the ZMQ streams and re-publishes them as LSL. A relay keeps `liblsl` out of
   every daemon, makes LSL optional per rig, and follows the precedent of
   mousewheeld's planned `relay` subcommand. It costs one hop of latency, which
   LSL's own timestamps would not see: the relay must stamp with the daemon's
   time, not its own.
2. **Which clock the outlet gives LSL.** LSL's `local_clock()` on Linux is
   `steady_clock`, which is `CLOCK_MONOTONIC`. A daemon on the same host could
   hand its `host_monotonic_ns` over unchanged; vstimd's process-relative clock
   could not.
3. **The mapping.** Events → marker streams (one per daemon or per topic?),
   carrying what? At least the topic, the sequence and the payload's key
   fields. Analog → sampled streams: lickd's per-port rates and readings,
   mousewheeld's position and velocity, vstimd's frame times.
4. **triald's record.** Whether triald adopts LSL's clock *model* for its own
   `.tdr` — a `ReadClock → {clock_id, now}` rpc in every daemon, polled in
   round trips, offsets written beside the events — independently of any
   outlet.
5. **The network.** LSL resolves streams by multicast. Whether that is allowed
   on a rig network, and how it sits beside mDNS.
6. **How much precision triald's record needs.** Where the precise record is
   the acquisition system's TTLs, this sets how much of the above is worth
   doing.

## First step

A spike on lickd, the newest daemon and the one with both events and analog
data. Publish `lick.onset` and `lick.offset` as a marker outlet and the
per-port rates as a sampled outlet, from a relay. Record them with LabRecorder
beside a second LSL stream on another host, and align with pyxdf. That answers
1–3 and 5 with numbers.
