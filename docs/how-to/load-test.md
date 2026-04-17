# How to load test with the k6 charm

The k6 charm lets you run [Grafana k6](https://grafana.com/docs/k6/) load tests on Kubernetes, with built-in Prometheus and Loki integration for results.

## 1. Write a load test

Writing k6 scripts is out of scope here — see [k6 docs](https://grafana.com/docs/k6/latest/examples/) for details. Here's a minimal example that pushes logs to Loki:

```javascript
import loki from 'k6/x/loki';
import { sleep } from 'k6';

export const options = {
    vus: 100,
    duration: "10m",
};

export default function () {
    const conf = loki.Config(`http://fake@${__ENV.LOKI_URL}:3100`);
    const client = loki.Client(conf);
    client.pushParameterized(4, 1024, 2048);
    sleep(1);
};
```

> **Tip:** use [template literals](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Template_literals) (`` `${__ENV.VAR}` ``) to make scripts configurable via environment variables.

## 2. Deploy the charms

Deploy k6 alongside the COS stack to collect metrics and logs:

```bash
juju deploy k6-k8s k6
juju deploy prometheus-k8s prometheus
juju deploy loki-k8s loki
juju deploy grafana-k8s grafana

juju relate k6 prometheus
juju relate k6 loki
juju relate prometheus:grafana-source grafana
juju relate loki:grafana-source grafana
```

Prometheus and Loki are optional but recommended — without them, test results are only visible in the unit logs.

## 3. Configure the test

There are two ways to provide a test script.

### Option A: Side-load via `juju config`

```bash
juju config k6 load-test=@my-test.js
juju config k6 environment="LOKI_URL=10.1.15.133,RATE=500"
```

### Option B: Relate a charm that provides tests

Other charms can send tests over the `k6_tests` relation using the `K6TestProvider` library. See the [k6_test library](https://github.com/canonical/k6-k8s-operator/blob/main/lib/charms/k6_k8s/v0/k6_test.py) for integration details.

## 4. Run the test

```bash
# Run the side-loaded test
juju run k6/leader start

# Run a test received from a related charm
juju run k6/leader start app=loki test=test.js

# List all available tests
juju run k6/leader list
```

Actions must be run on the **leader unit**.

## 5. Stop a running test

```bash
juju run k6/leader stop
```

## 6. Scale up for more load

If a single unit isn't generating enough load, scale the application:

```bash
juju scale-application k6 3
```

The charm automatically splits the test across all units using k6's [execution segments](https://grafana.com/docs/k6/latest/using-k6/k6-options/reference/#execution-segment). Each unit runs a proportional slice of the total workload — VUs, iterations, and arrival rates are all partitioned by k6 itself. No changes to the test script are needed.
