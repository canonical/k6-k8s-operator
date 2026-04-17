# Reference

## Actions

| Action | Leader only | Description |
|---|---|---|
| `start` | Yes | Run a load test. Optionally pass `app` and `test` to select a relation-provided script. |
| `stop` | Yes | Stop a running load test on all units. |
| `list` | Yes | List all available test scripts. |

## Configuration

| Option | Type | Description |
|---|---|---|
| `load-test` | string | A k6 script to side-load. Set with `juju config k6 load-test=@file.js`. |
| `environment` | string | Comma-separated `KEY=VALUE` pairs passed to k6 as `-e` flags. |

## Relations

| Relation | Interface | Direction | Description |
|---|---|---|---|
| `k6` | `k6_peers` | peer | Internal peer relation for coordinating tests across units. |
| `send-remote-write` | `prometheus_remote_write` | requires | Send test metrics to Prometheus. Limit: 1. |
| `logging` | `loki_push_api` | requires | Send test logs to Loki. Limit: 1. |
| `receive-k6-tests` | `k6_tests` | requires | Receive test scripts and environment from other charms. |
| `service-mesh` | `service_mesh` | requires | Integrate with Istio service mesh. Limit: 1. |

## OCI image

Default: `ubuntu/xk6:0-24.04`
