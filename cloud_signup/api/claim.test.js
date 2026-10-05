'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createHandler } = require('./claim');

const ENV = { UPSTASH_REDIS_REST_URL: 'https://r.upstash.io', UPSTASH_REDIS_REST_TOKEN: 'tok' };
const TOKEN = '1800000600.0123456789abcdef';

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

function setup({ env = ENV, response } = {}) {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init, payload: JSON.parse(init.body) });
    return response || { ok: true, json: async () => [{ result: 1 }, { result: 1 }, { result: 'OK' }] };
  };
  const handler = createHandler({ fetchImpl, env, now: () => 1_800_000_000_000, randomId: () => 'a'.repeat(32) });
  return { handler, calls };
}

test('queues a valid claim, expires the key and keeps the last 50', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body() }, res);

  assert.equal(res.code, 200);
  assert.deepEqual(res.body, { ok: true, id: 'a'.repeat(32) });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'https://r.upstash.io/pipeline');
  assert.equal(calls[0].init.headers.Authorization, 'Bearer tok');
  const [push, expire, trim] = calls[0].payload;
  assert.equal(push[0], 'RPUSH');
  assert.equal(push[1], 'fitrace:claims:gym-a');
  assert.deepEqual(expire, ['EXPIRE', 'fitrace:claims:gym-a', 900]);
  assert.deepEqual(trim, ['LTRIM', 'fitrace:claims:gym-a', -50, -1]);
  assert.deepEqual(JSON.parse(push[2]), {
    id: 'a'.repeat(32), venue: 'gym-a', station: 1, token: TOKEN, name: 'Amy',
    avatar_base64: null, received_at: 1_800_000_000,
  });
});

test('does not verify the token signature (the cloud has no secret)', async () => {
  const { handler, calls } = setup();
  const res = fakeRes();
  await handler({ method: 'POST', body: body({ token: '5.ffffffffffffffff' }) }, res);
  assert.equal(res.code, 200);
  assert.equal(calls.length, 1);
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

test('missing Upstash configuration is a 500', async () => {
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
