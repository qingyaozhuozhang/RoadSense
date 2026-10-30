/* Simulate phone foreground/background throttling and stop behavior in JS. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function preparePhone() {
  const code = fs.readFileSync(path.join(__dirname, '../frontend/static/js/phone_sender.js'), 'utf8');
  const nodes = new Map();
  const timers = [];
  const requests = [];
  const watchers = [];
  const events = new Map();
  let now = 2000;

  function element(id) {
    if (!nodes.has(id)) {
      nodes.set(id, {
        textContent: '', hidden: false, disabled: false,
        listeners: {}, classList: { toggle() {} },
        addEventListener(name, callback) { this.listeners[name] = callback; },
      });
    }
    return nodes.get(id);
  }

  const document = { hidden: false, getElementById: element,
    addEventListener(name, callback) { events.set(name, callback); } };
  const navigator = { geolocation: {
    watchPosition(success, error) { watchers.push({ success, error }); return 1; },
    getCurrentPosition() {}, clearWatch() {},
  } };
  const window = {
    createRoadSenseMap() { return { update() {} }; },
    isSecureContext: true,
    addEventListener(name, callback) { events.set(name, callback); },
  };
  class FakeDate extends Date { static now() { return now; } }
  const context = {
    document, navigator, window, Date: FakeDate,
    setInterval(callback, delay) { timers.push({ callback, delay }); },
    fetch: async (url, options) => {
      requests.push({ url, options });
      return { ok: true, json: async () => ({ active_source: 'phone' }) };
    },
  };
  vm.runInNewContext(code, context);
  const flush = () => new Promise(resolve => setImmediate(resolve));
  return { element, document, requests, timers, watchers, flush,
    tick(ms) { now += ms; for (const timer of timers) timer.callback(); },
    position() { return { timestamp: now, coords: { latitude: 30.1, longitude: 114.2, accuracy: 20 } }; },
  };
}

test('the phone sends every half second while the tab is backgrounded, until stopped', async () => {
  const phone = preparePhone();
  phone.element('sender-start').listeners.click();
  phone.watchers[0].success(phone.position());
  await phone.flush();
  assert.equal(phone.requests.length, 1);
  assert.ok(phone.timers.some(timer => timer.delay === 500), 'missing half-second cadence');

  phone.document.hidden = true;
  phone.tick(500);
  await phone.flush();
  assert.equal(phone.requests.length, 2, 'background tab should attempt the latest fix');

  phone.element('sender-stop').listeners.click();
  phone.tick(500);
  await phone.flush();
  assert.equal(phone.requests.length, 2, 'explicit stop must halt position uploads');
});
