'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { makeSignupToken, verifySignupToken } = require('./token');

// Cross-language contract: tests/unit/hub/test_signup_token.py asserts this
// exact vector against the hub's Python implementation.
const VECTOR = { secret: 's3cret', venue: 'gym-a', exp: 1800000300, nonce: '0a1b2c3d' };
const VECTOR_TOKEN = '1800000300.0a1b2c3d.3a57d57715bb491c';

test('matches the cross-language test vector produced by the hub', () => {
  assert.equal(
    makeSignupToken(VECTOR.secret, VECTOR.venue, VECTOR.exp, VECTOR.nonce),
    VECTOR_TOKEN,
  );
  assert.equal(verifySignupToken('s3cret', 'gym-a', VECTOR_TOKEN, 1800000000), 'ok');
});

test('expired tokens are reported as expired, bad signatures as invalid', () => {
  assert.equal(verifySignupToken('s3cret', 'gym-a', VECTOR_TOKEN, 1800000300), 'ok');
  assert.equal(verifySignupToken('s3cret', 'gym-a', VECTOR_TOKEN, 1800000301), 'expired');
  assert.equal(verifySignupToken('other', 'gym-a', VECTOR_TOKEN, 1800000000), 'invalid');
  assert.equal(verifySignupToken('s3cret', 'gym-b', VECTOR_TOKEN, 1800000000), 'invalid');
});

test('tampered expiry, nonce or signature is invalid', () => {
  const [exp, nonce, sig] = VECTOR_TOKEN.split('.');
  assert.equal(verifySignupToken('s3cret', 'gym-a', `${Number(exp) + 9999}.${nonce}.${sig}`, 1800000000), 'invalid');
  assert.equal(verifySignupToken('s3cret', 'gym-a', `${exp}.deadbeef.${sig}`, 1800000000), 'invalid');
  assert.equal(verifySignupToken('s3cret', 'gym-a', `${exp}.${nonce}.${'0'.repeat(16)}`, 1800000000), 'invalid');
});

test('malformed tokens are invalid rather than throwing', () => {
  for (const bad of ['', 'abc', '1.2', '1.2.3.4', null, undefined, 5, `${1800000300}..abc`]) {
    assert.equal(verifySignupToken('s3cret', 'gym-a', bad, 1800000000), 'invalid');
  }
});
