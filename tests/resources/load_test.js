// k6 load test that pushes logs to Loki via the xk6-loki extension
// and generates HTTP metrics forwarded to Prometheus via remote write.
//
// Expected environment variables (set via `juju config k6 environment=...`):
//   LOKI_URL    – base URL of the Loki instance, e.g. http://loki-0.loki-endpoints:3100
//   TARGET_URL  – HTTP endpoint to probe, e.g. http://prometheus-0.prometheus-endpoints:9090
import loki from 'k6/x/loki';
import http from 'k6/http';
import { check, sleep } from 'k6';

const LOKI_URL = __ENV.LOKI_URL || 'http://localhost:3100';
const TARGET_URL = __ENV.TARGET_URL || 'http://localhost:9090';

const conf = new loki.Config(LOKI_URL);
const client = new loki.Client(conf);

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    // Push a small batch of logs to Loki via xk6-loki
    let res = client.pushParameterized(2, 1024, 2048);
    check(res, { 'successful loki push': (r) => r.status == 204 });

    // HTTP request to generate metrics forwarded to Prometheus
    const httpRes = http.get(`${TARGET_URL}/-/healthy`);
    check(httpRes, { 'status is 200': (r) => r.status === 200 });

    sleep(1);
}
