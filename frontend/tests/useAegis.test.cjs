const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const ts = require("typescript");
const React = require("react");
const { act, create } = require("react-test-renderer");

// Run the actual TypeScript modules with Node's test runner, without a second bundler.
require.extensions[".ts"] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText, filename);
};
global.window = { location: { host: "localhost:3000", hostname: "localhost", protocol: "http:" } };
const { useAegis } = require("../src/lib/useAegis.ts");
const { DemoStore } = require("../src/lib/fixtures.ts");
const example = { ...new DemoStore().list()[0], agreement_id: "live-agreement" };
const emptyLedger = { entries: [], balances: { buyer_vault: 0, worker_payable: 0 }, balanced: true };
const response = (body, status = 200) => new Response(JSON.stringify(body), { status });

async function mount(t, fetch) {
  const originalFetch = global.fetch;
  global.fetch = fetch;
  let value;
  let root;
  function Harness() { value = useAegis(); return null; }
  await act(async () => { root = create(React.createElement(Harness)); });
  t.after(async () => {
    await act(async () => root.unmount());
    global.fetch = originalFetch;
  });
  return () => value;
}

function backend(contracts = [], ledger = emptyLedger) {
  return async (url) => {
    if (url.endsWith("/health")) return response({ status: "ok", paypal_mode: "mock", mock: true });
    if (url.endsWith("/contracts")) return response({ contracts, count: contracts.length });
    if (url.endsWith("/ledger")) return response(ledger);
    throw new Error(`Unexpected request ${url}`);
  };
}

test("empty live contracts and zero balances remain live data", async (t) => {
  const state = await mount(t, backend());
  assert.equal(state().connection, "online");
  assert.equal(state().demoMode, false);
  assert.deepEqual(state().contracts, []);
  assert.deepEqual(state().ledger, emptyLedger);
  assert.equal(state().selected, null);
});

test("failed requests report offline, preserve the last snapshot and recover", async (t) => {
  const state = await mount(t, backend([example]));
  const snapshot = state().contracts;
  global.fetch = async () => response({ detail: "unavailable" }, 503);
  await act(async () => state().refresh());
  assert.equal(state().connection, "offline");
  assert.equal(state().demoMode, false);
  assert.equal(state().contracts, snapshot);
  assert.match(state().error, /503/);
  global.fetch = backend();
  await act(async () => state().refresh());
  assert.equal(state().connection, "online");
  assert.deepEqual(state().contracts, []);
  assert.equal(state().error, null);
});

test("a cold start has a six-second deadline and polling never overlaps", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
  let calls = 0;
  const signals = [];
  const state = await mount(t, (_url, { signal }) => {
    calls += 1;
    signals.push(signal);
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))));
  });
  assert.equal(calls, 3);
  await act(async () => { t.mock.timers.tick(3000); });
  assert.equal(calls, 3);
  assert.equal(state().connection, "connecting");
  await act(async () => { t.mock.timers.tick(3000); });
  assert.equal(state().connection, "offline");
  assert(signals.every((s) => s.aborted));
  assert.equal(state().contracts.length, 0);
  global.fetch = backend([example]);
  await act(async () => { t.mock.timers.tick(3000); });
  assert.equal(state().connection, "online");
  assert.equal(state().contracts[0].agreement_id, example.agreement_id);
});

test("demo mode is explicit and ignores a late live response", async (t) => {
  const pending = [];
  let calls = 0;
  const state = await mount(t, (url) => {
    calls += 1;
    return new Promise((resolve) => pending.push(() => backend([example])(url).then(resolve)));
  });
  await act(async () => state().enableDemo());
  assert.equal(state().demoMode, true);
  assert.equal(state().contracts.length, 3);
  assert(state().contracts.every((c) => c.agreement_id.startsWith("demo-")));
  await act(async () => Promise.all(pending.map((resolve) => resolve())));
  assert.equal(state().demoMode, true);
  assert(state().contracts.every((c) => c.agreement_id.startsWith("demo-")));
  await act(async () => state().refresh());
  assert.equal(calls, 3, "demo refreshes must not call the live API");
  assert(state().getDemoDetail(state().contracts[0].agreement_id));
  global.fetch = backend();
  await act(async () => state().disableDemo());
  assert.equal(state().demoMode, false);
  assert.equal(state().connection, "online");
  assert.deepEqual(state().contracts, []);
  assert.equal(state().selected, null);
});

test("seeding sends a real mutation then refreshes contracts and ledger", async (t) => {
  let seeded = false;
  const requests = [];
  const state = await mount(t, async (url, init) => {
    requests.push([url, init.method]);
    if (url.endsWith("/demo/scenarios/all")) {
      assert.equal(init.method, "POST");
      seeded = true;
      return response({ contracts: [example], count: 1 });
    }
    return backend(seeded ? [example] : [])(url);
  });
  await act(async () => state().seed());
  assert.equal(state().connection, "online");
  assert.equal(state().contracts[0].agreement_id, example.agreement_id);
  assert.equal(state().busy, null);
  assert.equal(requests.filter(([url]) => url.endsWith("/ledger")).length, 2);
});

test("a rejected seed is shown as an action error without changing API connectivity", async (t) => {
  const state = await mount(t, backend());
  global.fetch = async () => response({ detail: "mock only" }, 400);
  await act(async () => state().seed());
  assert.equal(state().connection, "online");
  assert.match(state().error, /400/);
  assert.equal(state().busy, null);
  assert.deepEqual(state().contracts, []);
});

test("disabled WebSockets discard pending tickets and late events", async (t) => {
  const { useLedgerWebSocket } = require("../src/lib/useLedgerWebSocket.ts");
  const originalFetch = global.fetch;
  const originalWebSocket = global.WebSocket;
  const sockets = [];
  class Socket {
    static OPEN = 1;
    readyState = 1;
    constructor(url) { this.url = url; sockets.push(this); }
    close() { this.readyState = 3; this.onclose?.(); }
    send() {}
  }
  global.WebSocket = Socket;
  let resolveTicket;
  global.fetch = () => new Promise((resolve) => { resolveTicket = resolve; });
  let snapshots = 0;
  let status;
  function Harness({ enabled }) {
    status = useLedgerWebSocket({ onSnapshot: () => { snapshots += 1; } }, enabled).status;
    return null;
  }
  let root;
  await act(async () => { root = create(React.createElement(Harness, { enabled: true })); });
  t.after(async () => {
    await act(async () => root.unmount());
    global.fetch = originalFetch;
    global.WebSocket = originalWebSocket;
  });
  await act(async () => root.update(React.createElement(Harness, { enabled: false })));
  await act(async () => resolveTicket(response({ ticket: "old-ticket" })));
  assert.equal(sockets.length, 0, "a ticket arriving after demo mode must not open a socket");
  assert.equal(status, "disconnected");
  global.fetch = async () => response({ ticket: "fresh-ticket" });
  await act(async () => root.update(React.createElement(Harness, { enabled: true })));
  assert.equal(sockets.length, 1);
  assert.match(sockets[0].url, /ticket=fresh-ticket/);
  const frame = { data: JSON.stringify({ event_type: "INITIAL_SNAPSHOT", data: { contracts: [] } }) };
  await act(async () => { sockets[0].onopen(); sockets[0].onmessage(frame); });
  assert.equal(status, "connected");
  assert.equal(snapshots, 1);
  await act(async () => root.update(React.createElement(Harness, { enabled: false })));
  await act(async () => { sockets[0].onopen(); sockets[0].onmessage(frame); });
  assert.equal(status, "disconnected");
  assert.equal(snapshots, 1, "superseded sockets must not publish live events");
});

test("polling keeps the selected contract's report and history on screen", async (t) => {
  const row = { ...example, history: undefined, arbitration_report: undefined };
  const detail = {
    ...row,
    arbitration_report: { composite_score: 0.98, ast_safe: true },
    transitions: [{ from_state: null, to_state: "AGREEMENT_DRAFTED", created_at: "2026-11-11T00:00:00Z" }],
  };
  let detailFetches = 0;
  const list = backend([row]);
  const seen = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    if (url.endsWith(`/contracts/${row.agreement_id}`)) { detailFetches += 1; return response(detail); }
    return list(url, init);
  };
  let value;
  let root;
  function Harness() {
    value = useAegis();
    if (value.selected) seen.push(value.selected.arbitration_report ? "full" : "slim");
    return null;
  }
  await act(async () => { root = create(React.createElement(Harness)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 10)); });
  t.after(async () => { await act(async () => root.unmount()); global.fetch = originalFetch; });
  assert.equal(seen.at(-1), "full");
  const afterLoad = seen.length;
  await act(async () => value.refresh());
  await act(async () => value.refresh());
  assert.ok(!seen.slice(afterLoad).includes("slim"), `selected blanked during a poll: ${seen.join(" ")}`);
  assert.equal(value.selected.history.length, 1);
  assert.equal(detailFetches, 1, "an unchanged contract should not be re-fetched on every poll");
});
