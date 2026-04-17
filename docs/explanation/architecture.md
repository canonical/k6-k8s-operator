# Architecture

This document explains the internal design of the k6 charm — how it coordinates load tests across multiple units.

## Overview

The k6 charm wraps [Grafana k6](https://github.com/grafana/k6) in a Juju operator for Kubernetes. The charm's main challenges are:

1. Running a test across all units simultaneously (not just the leader).
2. Splitting the load proportionally when scaled to multiple units.

## Test lifecycle

All coordination happens through the **peer relation** (`k6`). The app databag holds test configuration; each unit's databag holds its status (`idle` or `busy`).

### Starting a test

```
User ─── juju run k6/leader start ───► Leader
```

1. The user runs the `start` action on the leader.
2. The leader writes the test configuration (`script_path`, `labels`, `status: idle`) to the **app** peer databag.
3. This triggers `relation-changed` on every unit.

### Unit startup

When a unit sees test configuration in the app databag with `status: idle`:

1. It builds a Pebble layer with the `k6 run` command (including execution segment flags if multi-unit).
2. It starts k6 via Pebble (k6 starts in `--paused` mode).
3. It sets its own unit status to `busy` in the peer databag.

### Synchronized resume

The leader is woken up by `relation-changed` each time a unit sets `busy`. Once **all** units report `busy`:

1. The leader sets the app status to `busy`.
2. The leader sends an HTTP `PATCH` to each unit's k6 REST API (`/status`) to resume the paused test.
3. All units begin generating load at the same time.

### Test completion

When k6 finishes on a unit:

1. The Pebble command includes `; pebble notify k6.com/done` after `k6 run`.
2. The Pebble custom notice fires, and the unit sets its status back to `idle`.
3. When all units are `idle`, the leader clears the app databag, completing the cycle.

```
  Leader                    Unit 1                    Unit 2
    │                         │                         │
    ├─ write app data ────────┼─────────────────────────┤
    │  (script, status:idle)  │                         │
    │                         │                         │
    │  ◄── relation-changed ──┤                         │
    │                         ├─ start k6 (paused) ─────┤
    │                         ├─ set unit: busy         │
    │                         │                         ├─ start k6 (paused)
    │                         │                         ├─ set unit: busy
    │                         │                         │
    ├─ all units busy ────────┼─────────────────────────┤
    ├─ HTTP /resume ──────────►                         │
    ├─ HTTP /resume ──────────┼─────────────────────────►
    │                         │                         │
    │                      ...test runs...              │
    │                         │                         │
    │                         ├─ pebble notify done     │
    │                         ├─ set unit: idle         │
    │                         │                         ├─ pebble notify done
    │                         │                         ├─ set unit: idle
    │                         │                         │
    ├─ all units idle ────────┼─────────────────────────┤
    ├─ clear app data         │                         │
```

## Load splitting with execution segments

When the charm is scaled to multiple units, each unit runs only a **fraction** of the total test using k6's native [`--execution-segment`](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#execution-segment) feature.

For N units, each unit i receives:

```
--execution-segment 'i/N:(i+1)/N'
--execution-segment-sequence '0,1/N,2/N,...,1'
```

k6 internally partitions VUs, iterations, and arrival rates proportionally. This works correctly with **all** executor types (`constant-vus`, `ramping-vus`, `constant-arrival-rate`, etc.) without needing to parse the test script.

### Unit index assignment

Each unit derives its index by sorting all peer unit names lexicographically. This is deterministic (all units produce the same ordering) and robust to non-contiguous unit numbers (e.g., after a scale-down removes `k6/2`, units `k6/0`, `k6/1`, `k6/3` map to indices 0, 1, 2).

For a single-unit deployment, no execution segment flags are passed — k6 runs the full test.

## Integrations

| Relation | Interface | Purpose |
|---|---|---|
| `send-remote-write` | `prometheus_remote_write` | Send test metrics to Prometheus |
| `logging` | `loki_push_api` | Send test logs to Loki |
| `receive-k6-tests` | `k6_tests` | Receive test scripts from other charms |
| `service-mesh` | `service_mesh` | Integrate with Istio service mesh |
