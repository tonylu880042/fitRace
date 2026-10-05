'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dir = __dirname;
const zh = JSON.parse(fs.readFileSync(path.join(dir, 'locales', 'zh-TW.json'), 'utf8'));
const en = JSON.parse(fs.readFileSync(path.join(dir, 'locales', 'en.json'), 'utf8'));
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');

test('zh-TW and en define exactly the same keys, all non-empty', () => {
  assert.deepEqual(Object.keys(zh).sort(), Object.keys(en).sort());
  for (const dict of [zh, en]) {
    for (const [k, v] of Object.entries(dict)) assert.ok(typeof v === 'string' && v.trim(), k);
  }
});

test('every data-i18n key used by the page exists in both locales', () => {
  const keys = [...html.matchAll(/data-i18n(?:-[a-z]+)?="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(keys.length >= 5);
  for (const key of keys) {
    assert.ok(key in zh, `zh-TW missing ${key}`);
    assert.ok(key in en, `en missing ${key}`);
  }
});

test('every t("key") call in the page script exists in both locales', () => {
  const keys = [...html.matchAll(/\bt\(\s*'([^']+)'/g)].map((m) => m[1]);
  assert.ok(keys.length >= 3);
  for (const key of keys) {
    assert.ok(key in zh, `zh-TW missing ${key}`);
    assert.ok(key in en, `en missing ${key}`);
  }
});

test('the page itself hardcodes no Chinese text', () => {
  assert.equal(/[一-鿿]/.test(html), false);
});

test('the page picks a locale from navigator.language and defaults to zh-TW', () => {
  assert.match(html, /navigator\.language/);
  assert.match(html, /zh-TW/);
});
