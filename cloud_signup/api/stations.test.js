'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHandler } = require('./stations');
const { makeSignupToken } = require('../lib/token');

const SECRET = 's3cret';
const ENV = { UPSTASH_REDIS_REST_URL: 'https://r.upstash.io', UPSTASH_REDIS_REST_TOKEN: 'tok', FITRACE_CLOUD_SIGNUP_SECRET: SECRET };
const NOW_MS = 1_800_000_000_000;
const TOKEN = makeSignupToken(SECRET, 'gym-a', 1_800_000_300, '0a1b2c3d');
const SNAPSHOT = [
  { station: 1, label: 'Treadmill A', equipment_type: 'treadmill', available: false },
  { station: 2, label: null, equipment_type: 'treadmill', available: true },
];

function fakeRes() {
  return {
    code: null, body: null, headers: {},
    setHeader(k, v) { this.headers[k] = v; },
    status(c) { this.code = c; return this; },
    json(b) { this.body = b; return this; },
  };
}

function setup({ env = ENV, used = 0, snapshot = JSON.stringify(SNAPSHOT), fail = false } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const payload = JSON.parse(init.body);
    calls.push({ url, init, payload });
    if (fail) return { ok: false, json: async () => ({}) };
    if (payload[0] === 'EXISTS') return { ok: true, json: async () => ({ result: used }) };
    if (payload[0] === 'GET') return { ok: true, json: async () => ({ result: snapshot }) };
    return { ok: true, json: async () => ({ result: null }) };
  };
  return { handler: createHandler({ fetchImpl, env, now: () => NOW_MS }), calls };
}

const query = (o = {}) => ({ v: 'gym-a', t: TOKEN, ...o });

test('returns the published station snapshot for a valid unused token', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'GET', query: query() }, res);
  assert.equal(res.code, 200);
  assert.deepEqual(res.body, { stations: SNAPSHOT });
  assert.deepEqual(calls[0].payload, ['EXISTS', `fitrace:used:${TOKEN}`]);
  assert.deepEqual(calls[1].payload, ['GET', 'fitrace:stations:gym-a']);
  assert.equal(res.headers['Cache-Control'], 'no-store');
});

test('a missing snapshot is an empty list, not an error', async () => {
  const { handler } = setup({ snapshot: null });
  const res = fakeRes();
  await handler({ method: 'GET', query: query() }, res);
  assert.deepEqual(res.body, { stations: [] });
});

test('an unparseable snapshot is an empty list', async () => {
  const { handler } = setup({ snapshot: '{nope' });
  const res = fakeRes();
  await handler({ method: 'GET', query: query() }, res);
  assert.deepEqual(res.body, { stations: [] });
});

test('only GET is allowed', async () => {
  const { handler } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', query: query() }, res);
  assert.equal(res.code, 405);
});

test('bad venue or malformed token is a 400 and nothing is read', async () => {
  for (const q of [query({ v: 'a b' }), query({ t: 'nope' }), query({ t: undefined }), {}]) {
    const { handler, calls } = setup();
    const res = fakeRes();
    await handler({ method: 'GET', query: q }, res);
    assert.equal(res.code, 400);
    assert.equal(calls.length, 0);
  }
});

test('forged and expired tokens are rejected', async () => {
  const forged = setup();
  const res1 = fakeRes();
  await forged.handler({ method: 'GET', query: query({ t: '1800000300.0a1b2c3d.0000000000000000' }) }, res1);
  assert.deepEqual([res1.code, res1.body], [400, { error: 'token' }]);

  const expired = setup();
  const res2 = fakeRes();
  await expired.handler({ method: 'GET', query: query({ t: makeSignupToken(SECRET, 'gym-a', 1_799_999_999, 'aa') }) }, res2);
  assert.deepEqual([res2.code, res2.body], [400, { error: 'expired' }]);
  assert.equal(expired.calls.length, 0);
});

test('an already used token is a 409 and the snapshot is not served', async () => {
  const { handler, calls } = setup({ used: 1 });
  const res = fakeRes();
  await handler({ method: 'GET', query: query() }, res);
  assert.deepEqual([res.code, res.body], [409, { error: 'used' }]);
  assert.equal(calls.filter((c) => c.payload[0] === 'GET').length, 0);
});

test('KV env names are accepted; missing config is a 500; upstream failure is a 502', async () => {
  const kv = setup({ env: { KV_REST_API_URL: 'https://kv.io', KV_REST_API_TOKEN: 'kvtok', FITRACE_CLOUD_SIGNUP_SECRET: SECRET } });
  const r1 = fakeRes();
  await kv.handler({ method: 'GET', query: query() }, r1);
  assert.equal(r1.code, 200);
  assert.ok(kv.calls.every((c) => c.url === 'https://kv.io' && c.init.headers.Authorization === 'Bearer kvtok'));

  const none = setup({ env: {} });
  const r2 = fakeRes();
  await none.handler({ method: 'GET', query: query() }, r2);
  assert.deepEqual([r2.code, r2.body], [500, { error: 'server_misconfigured' }]);

  const down = setup({ fail: true });
  const r3 = fakeRes();
  await down.handler({ method: 'GET', query: query() }, r3);
  assert.equal(r3.code, 502);
});
