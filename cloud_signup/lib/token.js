'use strict';

const crypto = require('node:crypto');

// Same algorithm as hub_server/usecases/signup_token.py; both sides assert the
// same test vector so they cannot drift.
function signature(secret, venue, exp, nonce) {
  return crypto
    .createHmac('sha256', secret)
    .update(`${venue}|${exp}|${nonce}`)
    .digest('hex')
    .slice(0, 16);
}

function makeSignupToken(secret, venue, exp, nonce) {
  return `${exp}.${nonce}.${signature(secret, venue, exp, nonce)}`;
}

// Returns 'ok' | 'expired' | 'invalid'.
function verifySignupToken(secret, venue, token, nowEpochS) {
  if (typeof token !== 'string') return 'invalid';
  const parts = token.split('.');
  if (parts.length !== 3) return 'invalid';
  const [expText, nonce, sig] = parts;
  if (!/^[0-9]{1,12}$/.test(expText) || !nonce) return 'invalid';
  const expected = Buffer.from(signature(secret, venue, Number(expText), nonce));
  const given = Buffer.from(sig);
  if (given.length !== expected.length || !crypto.timingSafeEqual(given, expected)) return 'invalid';
  return nowEpochS <= Number(expText) ? 'ok' : 'expired';
}

module.exports = { makeSignupToken, verifySignupToken };
