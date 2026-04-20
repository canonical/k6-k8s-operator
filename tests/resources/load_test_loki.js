// Lightweight k6 load test targeting the Loki readiness endpoint.
// Generates HTTP traffic and console output that are forwarded to Loki
// as log entries via --log-output=loki.
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    const res = http.get(
        'http://loki-0.loki-endpoints:3100/ready',
    );
    const ok = check(res, {
        'loki is ready': (r) => r.status === 200,
    });
    console.log(`loki readiness check: status=${res.status} ok=${ok}`);
    sleep(1);
}
