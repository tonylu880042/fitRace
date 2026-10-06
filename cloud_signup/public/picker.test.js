'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

function fn(name) {
  const m = script.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}`));
  assert.ok(m, `${name} not found`);
  return m[0];
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(['availableStations', 'isSelectable', 'autoSelection'].map(fn).join('\n'), ctx);

const s = (station, available) => ({ station, label: null, equipment_type: 'treadmill', available });

test('exactly one available station is selected automatically', () => {
  assert.equal(ctx.autoSelection([s(1, false), s(2, true)]), 2);
  assert.equal(ctx.autoSelection([s(1, true)]), 1);
});

test('zero or several available stations are never auto-selected', () => {
  assert.equal(ctx.autoSelection([]), null);
  assert.equal(ctx.autoSelection([s(1, false), s(2, false)]), null);
  assert.equal(ctx.autoSelection([s(1, true), s(2, true)]), null);
});

test('busy stations are disabled while another one is free', () => {
  const list = [s(1, false), s(2, true)];
  assert.equal(ctx.isSelectable(list, list[0]), false);
  assert.equal(ctx.isSelectable(list, list[1]), true);
});

test('when nothing is free every assigned station can still be picked (next round)', () => {
  const list = [s(1, false), s(2, false)];
  assert.equal(ctx.isSelectable(list, list[0]), true);
  assert.equal(ctx.isSelectable(list, list[1]), true);
});

test('the claim carries the picked station and no longer reads it from the URL', () => {
  assert.equal(/params\.get\('s'\)/.test(script), false);
  assert.match(script, /station: selectedStation/);
});
