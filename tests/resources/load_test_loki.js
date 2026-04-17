// Lightweight k6 load test targeting the Loki readiness endpoint.
// Generates HTTP traffic and console output that are forwarded to Loki
// as log entries.
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    const res = http.get(
        'http://loki-k8s-0.loki-k8s-endpoints:3100/ready',
    );
    check(res, {
        'loki is ready': (r) => r.status === 200,
    });
    sleep(1);
}
