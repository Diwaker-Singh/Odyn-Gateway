// k6 limit proof — LOCAL STUB ONLY (M5). Never against production/live upstream.
// Two scenarios, same server, different principals:
//   gentle: fresh principal, below limit -> every request must be 200.
//   burst:  saturated principal, above burst -> must see 429s, each carrying
//           an integer Retry-After; every response is 200 or well-formed 429.
// Env: GATEWAY_URL, AUTH_TOKEN_A (burst principal), AUTH_TOKEN_B (gentle).
// Usage: k6 run --no-usage-report loadtest/k6-limits.js
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

const seen429 = new Counter('gateway_429_total');

export const options = {
  thresholds: {
    checks: ['rate==1'],
    gateway_429_total: ['count>0'],
  },
  scenarios: {
    gentle: {
      executor: 'shared-iterations',
      vus: 1,
      iterations: 5,
      exec: 'gentle',
    },
    burst: {
      executor: 'shared-iterations',
      vus: 10,
      iterations: 60,
      exec: 'burst',
      startTime: '3s',
    },
  },
};

const URL = __ENV.GATEWAY_URL || 'http://127.0.0.1:8000/v1/chat';
if (!/^http:\/\/(127\.0\.0\.1|localhost):[0-9]+\/v1\/chat$/.test(URL)) {
  throw new Error('Local limit proof requires an explicit loopback gateway URL');
}
const BODY = JSON.stringify({
  model: 'default',
  messages: [{ role: 'user', content: 'k6 probe' }],
  max_tokens: 5,
});

function post(token) {
  return http.post(URL, BODY, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
  });
}

export function gentle() {
  const r = post(__ENV.AUTH_TOKEN_B);
  check(r, { 'gentle: 200': (x) => x.status === 200 });
}

export function burst() {
  const r = post(__ENV.AUTH_TOKEN_A);
  const wellformed429 =
    r.status === 429 && /^[1-9][0-9]*$/.test(String(r.headers['Retry-After']));
  if (r.status === 429) seen429.add(1);
  check(r, {
    'burst: 200 or 429+Retry-After': (x) => x.status === 200 || wellformed429,
  });
}
