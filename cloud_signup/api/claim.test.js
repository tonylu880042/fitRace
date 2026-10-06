'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHandler } = require('./claim');
const { makeSignupToken } = require('../lib/token');

const SECRET = 's3cret';
const ENV = {
  UPSTASH_REDIS_REST_URL: 'https://r.upstash.io',
  UPSTASH_REDIS_REST_TOKEN: 'tok',
  FITRACE_CLOUD_SIGNUP_SECRET: SECRET,
};
const NOW_MS = 1_800_000_000_000;
const TOKEN = makeSignupToken(SECRET, 'gym-a', 1, 1_800_000_300, '0a1b2c3d');

function fakeRes() {
  return {
    code: null, body: null, headers: {},
    setHeader(k, v) { this.headers[k] = v; },
    status(c) { this.code = c; return this; },
    json(b) { this.body = b; return this; },
  };
}

function body(overrides = {}) {
  return { venue: 'gym-a', station: 1, token: TOKEN, name: 'Amy', avatar_base64: null, ...overrides };
}

function setup({ env = ENV, response, used = false } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    const payload = JSON.parse(init.body);
    calls.push({ url, init, payload });
    if (!url.endsWith('/pipeline') && payload[0] === 'SET') {
      return { ok: true, json: async () => ({ result: used ? null : 'OK' }) };
    }
    if (!url.endsWith('/pipeline') && payload[0] === 'DEL') {
      return { ok: true, json: async () => ({ result: 1 }) };
    }
    return response || { ok: true, json: async () => [{ result: 1 }, { result: 1 }, { result: 'OK' }] };
  };
  const handler = createHandler({ fetchImpl, env, now: () => NOW_MS, randomId: () => 'a'.repeat(32) });
  return { handler, calls };
}

test('queues a valid claim, expires the key and keeps the last 50', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);

  assert.equal(res.code, 200);
  assert.deepEqual(res.body, { ok: true, id: 'a'.repeat(32) });
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[0].payload, ['SET', `fitrace:used:${TOKEN}`, '1', 'NX', 'EX', '900']);
  assert.equal(calls[1].url, 'https://r.upstash.io/pipeline');
  assert.equal(calls[1].init.headers.Authorization, 'Bearer tok');
  const [push, expire, trim] = calls[1].payload;
  assert.equal(push[0], 'RPUSH');
  assert.equal(push[1], 'fitrace:claims:gym-a');
  assert.deepEqual(expire, ['EXPIRE', 'fitrace:claims:gym-a', 900]);
  assert.deepEqual(trim, ['LTRIM', 'fitrace:claims:gym-a', -50, -1]);
  assert.deepEqual(JSON.parse(push[2]), {
    id: 'a'.repeat(32), venue: 'gym-a', station: 1, token: TOKEN, name: 'Amy',
    avatar_base64: null, received_at: 1_800_000_000,
  });
});

test('a forged token is rejected and nothing is queued', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body({ token: '1800000300.0a1b2c3d.0000000000000000' }) }, res);
  assert.equal(res.code, 400);
  assert.deepEqual(res.body, { error: 'token' });
  assert.equal(calls.length, 0);
});

test('an expired token is rejected', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  const expired = makeSignupToken(SECRET, 'gym-a', 1, 1_799_999_999, 'aa');
  await handler({ method: 'POST', body: body({ token: expired }) }, res);
  assert.equal(res.code, 400);
  assert.deepEqual(res.body, { error: 'expired' });
  assert.equal(calls.length, 0);
});

test('a token for another station is rejected', async () => {
  const { handler } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body({ station: 2 }) }, res);
  assert.equal(res.code, 400);
});

test('a token that was already used is a 409 and queues nothing', async () => {
  const { handler, calls } = setup({ used: true });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 409);
  assert.deepEqual(res.body, { error: 'used' });
  assert.equal(calls.filter((c) => c.url.endsWith('/pipeline')).length, 0);
});

test('if queueing fails the token is released so the person can retry', async () => {
  const { handler, calls } = setup({ response: { ok: false, status: 500, json: async () => ({}) } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 502);
  assert.deepEqual(calls[calls.length - 1].payload, ['DEL', `fitrace:used:${TOKEN}`]);
});

test('accepts a JSON string body', async () => {
  const { handler } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: JSON.stringify(body()) }, res);
  assert.equal(res.code, 200);
});

test('invalid input is a 400 with the failing field and nothing is queued', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body({ name: '' }) }, res);
  assert.equal(res.code, 400);
  assert.deepEqual(res.body, { error: 'name' });
  assert.equal(calls.length, 0);
});

test('malformed JSON string is a 400', async () => {
  const { handler } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: '{nope' }, res);
  assert.equal(res.code, 400);
});

test('only POST is allowed', async () => {
  const { handler } = setup();
  const res = fakeRes();
  await handler({ method: 'GET' }, res);
  assert.equal(res.code, 405);
  assert.equal(res.headers.Allow, 'POST');
});

test('missing Upstash or signing configuration is a 500', async () => {
  const { handler, calls } = setup({ env: {} });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 500);
  assert.equal(calls.length, 0);
});

test('Upstash failure surfaces as 502', async () => {
  const { handler } = setup({ response: { ok: false, status: 500, json: async () => ({}) } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 502);
});

test('an error entry inside the pipeline result is a 502', async () => {
  const { handler } = setup({ response: { ok: true, json: async () => [{ error: 'WRONGTYPE' }] } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 502);
});

test('missing signing secret is a 500', async () => {
  const { handler, calls } = setup({ env: { ...ENV, FITRACE_CLOUD_SIGNUP_SECRET: '' } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 500);
  assert.equal(calls.length, 0);
});

const SIGNING = { FITRACE_CLOUD_SIGNUP_SECRET: SECRET };

test('Vercel KV env names alone are accepted', async () => {
  const { handler, calls } = setup({
    env: { KV_REST_API_URL: 'https://kv.example.io', KV_REST_API_TOKEN: 'kvtok', ...SIGNING },
  });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 200);
  assert.ok(calls.length >= 2);
  for (const call of calls) {
    assert.ok(call.url.startsWith('https://kv.example.io'), call.url);
    assert.equal(call.init.headers.Authorization, 'Bearer kvtok');
  }
});

test('UPSTASH_* names win when both sets are present', async () => {
  const { handler, calls } = setup({
    env: { ...ENV, KV_REST_API_URL: 'https://kv.example.io', KV_REST_API_TOKEN: 'kvtok' },
  });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 200);
  for (const call of calls) {
    assert.ok(call.url.startsWith('https://r.upstash.io'), call.url);
    assert.equal(call.init.headers.Authorization, 'Bearer tok');
  }
});

test('KV URL without any token is a 500 server_misconfigured', async () => {
  const { handler, calls } = setup({ env: { KV_REST_API_URL: 'https://kv.example.io', ...SIGNING } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 500);
  assert.deepEqual(res.body, { error: 'server_misconfigured' });
  assert.equal(calls.length, 0);
});

test('no Redis configuration at all is a 500 server_misconfigured', async () => {
  const { handler } = setup({ env: { ...SIGNING } });
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);
  assert.equal(res.code, 500);
  assert.deepEqual(res.body, { error: 'server_misconfigured' });
});
