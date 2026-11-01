import { useEffect, useState } from "react";
import Logo from "./Logo";
import { usd, useCount } from "./lib";

type Settlement = { decision: string; capture_micros: number; buyer_released_micros: number; platform_fee_micros: number; paypal_rail_fee_micros: number; worker_net_payout_micros: number };
type Report = { s_pytest: number; s_ast: number; s_semantic: number; composite_score: number; passed_tests: number; total_tests: number; ast_safe: boolean; ast_violations: string[]; settlement: Settlement };
type Demo = { contract: { agreement_id: string; fsm_state: string; budget_micros: number }; report: Report; balances: Record<string, number> };
type Entry = { entry_id: string; debit_account: string; credit_account: string; amount_usd: string; description: string };

const SCENARIOS = [
  { key: "CLEAN_PASS", title: "Clean pass", desc: "Correct, safe code. Full capture." },
  { key: "PARTIAL_DEFECT", title: "Partial defect", desc: "Some tests fail. Proportional capture." },
  { key: "MALICIOUS_INJECTION", title: "Malicious injection", desc: "AST veto. Authorization voided." },
];
const BASE = ["AGREEMENT_DRAFTED", "PAYPAL_AUTH_LOCKED", "SANDBOX_EXECUTING", "ARBITRATION_AUDITING"];
const TAIL: Record<string, string[]> = {
  SETTLED_COMPLETE: ["FULL_CAPTURE_PENDING", "PAYPAL_CAPTURED", "PAYOUT_DISPATCHED", "SETTLED_COMPLETE"],
  ESCROW_VOIDED_REFUNDED: ["VOID_PENDING", "ESCROW_VOIDED_REFUNDED"],
  CAPTURE_DECLINED: ["FULL_CAPTURE_PENDING", "CAPTURE_DECLINED"],
  PAYOUT_FAILED_REVERSED: ["FULL_CAPTURE_PENDING", "PAYPAL_CAPTURED", "PAYOUT_FAILED_REVERSED"],
};
const label = (s: string) => s.replace(/_/g, " ").toLowerCase();

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, init);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function Meter({ name, v, weight }: { name: string; v: number; weight: string }) {
  return (
    <div className="meter">
      <div><span>{name}</span><em>{weight}</em><b>{v.toFixed(2)}</b></div>
      <div className="track"><i style={{ width: `${v * 100}%` }} /></div>
    </div>
  );
}

function Ring({ v }: { v: number }) {
  const n = useCount(v, true, 1100);
  const C = 2 * Math.PI * 52;
  return (
    <svg viewBox="0 0 120 120" className="ring">
      <circle cx="60" cy="60" r="52" className="bg" />
      <circle cx="60" cy="60" r="52" className="fg" strokeDasharray={C} strokeDashoffset={C * (1 - n)} />
      <text x="60" y="58" textAnchor="middle">{n.toFixed(2)}</text>
      <text x="60" y="76" textAnchor="middle" className="sub">composite S</text>
    </svg>
  );
}

export default function Dashboard() {
  const [online, setOnline] = useState<boolean | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [res, setRes] = useState<Demo | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [shown, setShown] = useState(0);

  useEffect(() => {
    api("/health").then(() => setOnline(true)).catch(() => setOnline(false));
  }, []);

  const path = res ? [...BASE, ...(TAIL[res.contract.fsm_state] ?? [res.contract.fsm_state])] : [];
  useEffect(() => {
    if (!path.length) return;
    setShown(0);
    const t = setInterval(() => setShown((x) => (x >= path.length ? (clearInterval(t), x) : x + 1)), 280);
    return () => clearInterval(t);
  }, [res]);

  async function run(key: string) {
    setBusy(key); setErr("");
    try {
      const d = await api<Demo>(`/demo/${key}`, { method: "POST" });
      const l = await api<{ entries: Entry[] }>("/ledger");
      setRes(d); setEntries(l.entries); setOnline(true);
    } catch (e) {
      setErr(String(e)); setOnline(false);
    } finally { setBusy(""); }
  }

  const s = res?.report.settlement;
  const budget = res?.contract.budget_micros ?? 1;
  const w = (v: number) => `${(v / budget) * 100}%`;

  return (
    <div className="dash">
      <header className="dnav">
        <a className="brand" href="#"><Logo /> AegisPay <span className="tag">Dashboard</span></a>
        <div className="right">
          <span className={`status ${online ? "up" : online === false ? "down" : ""}`}>{online ? "API online (mock PayPal)" : online === false ? "API offline" : "checking..."}</span>
          <a className="btn sm ghost" href="#">← Home</a>
        </div>
      </header>

      <main>
        <section className="intro">
          <h1>Mission <em>control</em></h1>
          <p>Pick a scenario. We create an agreement, lock the escrow, verify the code and settle, all against the mocked PayPal client.</p>
        </section>

        {online === false && (
          <div className="alert">Can't reach the backend. In another terminal run <code>uvicorn backend.app.main:app --port 8000</code>, then retry.</div>
        )}
        {err && online !== false && <div className="alert">{err}</div>}

        <section className="scen">
          {SCENARIOS.map((s) => (
            <button key={s.key} className={`sc ${busy === s.key ? "busy" : ""}`} disabled={!!busy} onClick={() => run(s.key)}>
              <b>{s.title}</b><span>{s.desc}</span><em>{busy === s.key ? "Running..." : "Run scenario →"}</em>
            </button>
          ))}
        </section>

        {!res && <div className="empty">No run yet. Choose a scenario above.</div>}

        {res && s && (
          <div className="out">
            <div className="panel wide">
              <h3>State machine</h3>
              <div className="fsm">
                {path.map((p, i) => (
                  <span key={p + i} className={`node ${i < shown ? "on" : ""} ${i === path.length - 1 ? "end" : ""} ${["ESCROW_VOIDED_REFUNDED", "CAPTURE_DECLINED", "PAYOUT_FAILED_REVERSED"].includes(p) ? "bad" : ""}`}>{label(p)}</span>
                ))}
              </div>
            </div>

            <div className="panel">
              <h3>Verification</h3>
              <div className="verif">
                <Ring v={res.report.composite_score} />
                <div className="meters">
                  <Meter name="Tests" v={res.report.s_pytest} weight="50%" />
                  <Meter name="AST safety" v={res.report.s_ast} weight="25%" />
                  <Meter name="Semantic" v={res.report.s_semantic} weight="25%" />
                </div>
              </div>
              <p className="muted">{res.report.passed_tests}/{res.report.total_tests} tests passed. {res.report.ast_safe ? "AST scan clean." : `AST violations: ${res.report.ast_violations.join("; ")}`}</p>
            </div>

            <div className="panel">
              <h3>Settlement <span className="pill">{s.decision.replace(/_/g, " ").toLowerCase()}</span></h3>
              <div className="bar">
                <i style={{ width: w(s.worker_net_payout_micros), background: "var(--blue)" }} />
                <i style={{ width: w(s.platform_fee_micros), background: "var(--navy)" }} />
                <i style={{ width: w(s.paypal_rail_fee_micros), background: "var(--sky-d)" }} />
                <i style={{ width: w(s.buyer_released_micros), background: "var(--line)" }} />
              </div>
              <ul className="legend">
                <li><u style={{ background: "var(--blue)" }} />Worker net <b>{usd(s.worker_net_payout_micros)}</b></li>
                <li><u style={{ background: "var(--navy)" }} />Platform fee <b>{usd(s.platform_fee_micros)}</b></li>
                <li><u style={{ background: "var(--sky-d)" }} />Payout rail <b>{usd(s.paypal_rail_fee_micros)}</b></li>
                <li><u style={{ background: "var(--line)" }} />Buyer released <b>{usd(s.buyer_released_micros)}</b></li>
              </ul>
            </div>

            <div className="panel wide">
              <h3>Ledger balances</h3>
              <div className="bals">
                {Object.entries(res.balances).map(([k, v]) => (
                  <div key={k}><span>{k.replace(/_/g, " ")}</span><b>{usd(v)}</b></div>
                ))}
              </div>
            </div>

            <div className="panel wide">
              <h3>Journal entries <span className="muted">({entries.length})</span></h3>
              <div className="tbl">
                <table>
                  <thead><tr><th>To (debit)</th><th>From (credit)</th><th>Amount</th><th>Note</th></tr></thead>
                  <tbody>
                    {entries.slice(-12).reverse().map((e) => (
                      <tr key={e.entry_id}><td>{e.debit_account}</td><td>{e.credit_account}</td><td>{e.amount_usd}</td><td>{e.description}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
