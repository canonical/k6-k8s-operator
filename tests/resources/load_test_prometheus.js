// Lightweight k6 load test targeting the Prometheus health endpoint.
// Generates HTTP metrics (k6_http_reqs, k6_http_req_duration, etc.)
// that are forwarded to Prometheus via remote write.
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    const res = http.get(
        'http://prometheus-k8s-0.prometheus-k8s-endpoints:9090/-/ready',
    );
    check(res, {
        'prometheus is ready': (r) => r.status === 200,
    });
    sleep(1);
}
