'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  validateClaimInput,
  claimKey,
  NAME_MAX,
  PHOTO_MAX_CHARS,
} = require('./validate');

const TOKEN = '1800000300.0a1b2c3d.0123456789abcdef';
const PHOTO = 'data:image/webp;base64,UklGRhoAAABXRUJQVlA4TA0AAAAvAAAAEAcQERGIiP4H';

function good(overrides = {}) {
  return { venue: 'gym-a', station: 1, token: TOKEN, name: 'Amy', avatar_base64: PHOTO, ...overrides };
}

test('accepts a well-formed claim and normalises it', () => {
  const r = validateClaimInput(good({ station: '2', name: '  Amy  ' }));
  assert.equal(r.ok, true);
  assert.deepEqual(r.value, { venue: 'gym-a', station: 2, token: TOKEN, name: 'Amy', avatar_base64: PHOTO });
});

test('photo is optional', () => {
  for (const photo of [undefined, null, '']) {
    const r = validateClaimInput(good({ avatar_base64: photo }));
    assert.equal(r.ok, true);
    assert.equal(r.value.avatar_base64, null);
  }
});

test('rejects non-object bodies', () => {
  for (const body of [null, undefined, 'x', 5, []]) {
    assert.deepEqual(validateClaimInput(body), { ok: false, error: 'body' });
  }
});

test('venue must be a short slug', () => {
  for (const venue of ['', 'a b', 'gym/a', 'x'.repeat(41), 5, undefined, 'a:b']) {
    assert.deepEqual(validateClaimInput(good({ venue })), { ok: false, error: 'venue' });
  }
});

test('station must be a positive integer', () => {
  for (const station of [0, -1, 1.5, 'abc', '', null, 1000, '1e2']) {
    assert.deepEqual(validateClaimInput(good({ station })), { ok: false, error: 'station' });
  }
});

test('token must look like <exp>.<nonce>.<16 hex>', () => {
  for (const token of ['', 'abc', '123.abc', '123.0a1b2c3d.0123456789ABCDEF', '.0a1b2c3d.0123456789abcdef', '1.2.3', '123.0123456789abcdef', 5, null]) {
    assert.deepEqual(validateClaimInput(good({ token })), { ok: false, error: 'token' });
  }
});

test('name must be 1-20 characters after trimming', () => {
  assert.equal(validateClaimInput(good({ name: 'x'.repeat(NAME_MAX) })).ok, true);
  assert.equal(validateClaimInput(good({ name: '王'.repeat(NAME_MAX) })).ok, true);
  for (const name of ['', '   ', 'x'.repeat(NAME_MAX + 1), 5, null, undefined]) {
    assert.deepEqual(validateClaimInput(good({ name })), { ok: false, error: 'name' });
  }
});

test('name length counts characters, not UTF-16 units', () => {
  assert.equal(validateClaimInput(good({ name: '😀'.repeat(NAME_MAX) })).ok, true);
  assert.equal(validateClaimInput(good({ name: '😀'.repeat(NAME_MAX + 1) })).ok, false);
});

test('photo must be a webp data URL no larger than 200KB', () => {
  for (const photo of ['data:image/png;base64,AAAA', 'AAAA', 'data:image/webp;base64,', 'data:image/webp;base64,@@@', 5]) {
    assert.deepEqual(validateClaimInput(good({ avatar_base64: photo })), { ok: false, error: 'photo' });
  }
  const prefix = 'data:image/webp;base64,';
  const atLimit = prefix + 'A'.repeat(PHOTO_MAX_CHARS - prefix.length);
  assert.equal(atLimit.length, PHOTO_MAX_CHARS);
  assert.equal(validateClaimInput(good({ avatar_base64: atLimit })).ok, true);
  assert.deepEqual(validateClaimInput(good({ avatar_base64: atLimit + 'A' })), { ok: false, error: 'photo' });
});

test('claimKey namespaces by venue', () => {
  assert.equal(claimKey('gym-a'), 'fitrace:claims:gym-a');
});

test('photo may also be a JPEG data URL (browsers that cannot encode WebP)', () => {
  const jpeg = 'data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ==';
  const r = validateClaimInput(good({ avatar_base64: jpeg }));
  assert.equal(r.ok, true);
  assert.equal(r.value.avatar_base64, jpeg);
  assert.deepEqual(validateClaimInput(good({ avatar_base64: 'data:image/jpeg;base64,@@@' })), { ok: false, error: 'photo' });
  assert.deepEqual(validateClaimInput(good({ avatar_base64: 'data:image/png;base64,iVBORw0KGgo=' })), { ok: false, error: 'photo' });
  const prefix = 'data:image/jpeg;base64,';
  assert.deepEqual(validateClaimInput(good({ avatar_base64: prefix + 'A'.repeat(PHOTO_MAX_CHARS) })), { ok: false, error: 'photo' });
});
