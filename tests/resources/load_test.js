// Lightweight k6 load test that generates both HTTP metrics (forwarded to
// Prometheus via remote write) and console log entries (forwarded to Loki
// via --log-output=loki).
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '10s',
};

export default function () {
    const res = http.get('http://httpbin.org/get');
    const ok = check(res, {
        'status is 200': (r) => r.status === 200,
    });
    console.log(`http check: status=${res.status} ok=${ok}`);
    sleep(1);
}
