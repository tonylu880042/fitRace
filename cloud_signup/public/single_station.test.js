'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dir = __dirname;
const html = fs.readFileSync(path.join(dir, 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const zh = JSON.parse(fs.readFileSync(path.join(dir, 'locales', 'zh-TW.json'), 'utf8'));
const en = JSON.parse(fs.readFileSync(path.join(dir, 'locales', 'en.json'), 'utf8'));

function el() {
  const e = {
    hidden: false, disabled: false, _text: '', value: '', style: {}, dataset: {}, children: [], attrs: {}, listeners: {},
    appendChild(c) { this.children.push(c); },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(type, fn) { this.listeners[type] = fn; },
    click() {},
  };
  // Like the DOM, assigning textContent drops the element's children.
  Object.defineProperty(e, 'textContent', {
    get() { return this._text; },
    set(v) { this._text = v; this.children.length = 0; },
  });
  return e;
}

// Runs the real page script against stub DOM/fetch and returns what it did.
async function runPage(stations) {
  const elements = {};
  const get = (id) => elements[id] || (elements[id] = el());
  const posts = [];
  const ctx = {
    URLSearchParams, encodeURIComponent, JSON, Object, Promise, Math, Number, String, Array, console, setTimeout,
    location: { search: '?v=gym-a&t=1800000300.0a1b2c3d.0123456789abcdef' },
    navigator: { language: 'en-US' },
    Image: class {},
    URL: { createObjectURL() {}, revokeObjectURL() {} },
    document: {
      getElementById: get,
      querySelectorAll: () => [],
      createElement: () => el(),
      documentElement: {},
      title: '',
    },
    fetch: async (url, init) => {
      if (url.startsWith('locales/')) return { ok: true, json: async () => en };
      if (url.startsWith('/api/stations')) return { ok: true, json: async () => ({ stations }) };
      if (url === '/api/claim') { posts.push(JSON.parse(init.body)); return { ok: true, json: async () => ({ ok: true }) }; }
      throw new Error(`unexpected fetch ${url}`);
    },
  };
  vm.createContext(ctx);
  vm.runInContext(script, ctx);
  await new Promise((r) => setTimeout(r, 30));
  get('name').value = 'Amy';
  await get('form').listeners.submit({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 10));
  return { get, posts };
}

const st = (station, available, label = null) => ({ station, label, equipment_type: 'treadmill', available });

test('one available station: no picker, nothing extra, claim posts that station', async () => {
  const { get, posts } = await runPage([st(3, true)]);
  assert.equal(get('stations').children.length, 0);
  assert.equal(get('station-heading').hidden, true);
  assert.equal(get('station-note').textContent, '');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].station, 3);
});

test('one busy station: no picker, a single "you will be next" note, claim still posts it', async () => {
  const { get, posts } = await runPage([st(3, false)]);
  assert.equal(get('stations').children.length, 0);
  assert.equal(get('station-note').textContent, en.station_single_busy);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].station, 3);
});

test('two stations with one free: the picker is shown and the free one is selected', async () => {
  const { get, posts } = await runPage([st(1, false), st(2, true)]);
  assert.equal(get('stations').children.length, 2);
  assert.equal(get('station-heading').hidden, false);
  assert.equal(posts[0].station, 2);
});

test('two stations both free: picker shown, nothing auto-selected so the claim is not sent', async () => {
  const { get, posts } = await runPage([st(1, true), st(2, true)]);
  assert.equal(get('stations').children.length, 2);
  assert.equal(posts.length, 0);
  assert.equal(get('error').textContent, en.error_station);
});

test('zero stations: unchanged (no picker, no-stations error)', async () => {
  const { get, posts } = await runPage([]);
  assert.equal(get('stations').children.length, 0);
  assert.equal(posts.length, 0);
});

test('the new note exists in both locales', () => {
  assert.ok(zh.station_single_busy && en.station_single_busy);
});
