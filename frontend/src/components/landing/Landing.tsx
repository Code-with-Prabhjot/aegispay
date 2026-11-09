"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import Logo from "./Logo";
import { settle, usd, useCount, useInView, useReveal } from "./landingLib";
import "./landing.css";

const STATES = [
  "AGREEMENT_DRAFTED",
  "PAYPAL_AUTH_LOCKED",
  "SANDBOX_EXECUTING",
  "ARBITRATION_AUDITING",
  "PAYPAL_CAPTURED",
  "SETTLED_COMPLETE",
];

const STEPS = [
  { t: "Agree", d: "Buyer and worker agents sign an SLA: task, budget and deadline. Nothing moves yet.", k: "AGREEMENT_DRAFTED" },
  { t: "Lock", d: "The budget is placed on hold with a PayPal authorization. The buyer can't spend it twice, and the worker knows it exists.", k: "PAYPAL_AUTH_LOCKED" },
  { t: "Verify", d: "The worker's code runs in a sandbox. An AST scan, the buyer's tests and a semantic rubric produce one score.", k: "ARBITRATION_AUDITING" },
  { t: "Settle", d: "Full capture, partial capture or void, decided by the score. The worker is paid out and every cent is booked in a double-entry ledger.", k: "SETTLED_COMPLETE" },
];

const FEATURES = [
  ["Authorize, don't pay up front", "Funds are held, not spent. Capture only what the work earned, or void the rest."],
  ["Proof before payout", "Tests, a security scan and a rubric are blended into a score S, with a hard veto on unsafe code."],
  ["Proportional settlement", "Between 0.60 and 0.90, the worker earns a sliding share. No all-or-nothing disputes."],
  ["Ledger that always balances", "Integer micro-dollar double-entry bookkeeping. Debits equal credits, always."],
  ["Idempotent by design", "Deterministic request IDs mean a retry never double-captures or double-pays."],
  ["Built for agents", "Machines hire machines. No humans in the loop to argue about a refund."],
];

function Tilt({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const move = (e: React.MouseEvent) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5;
    const y = (e.clientY - r.top) / r.height - 0.5;
    el.style.transform = `perspective(700px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg) translateY(-4px)`;
    el.style.setProperty("--mx", `${(x + 0.5) * 100}%`);
    el.style.setProperty("--my", `${(y + 0.5) * 100}%`);
  };
  return (
    <div
      ref={ref}
      className={`tilt ${className}`}
      onMouseMove={move}
      onMouseLeave={() => ref.current && (ref.current.style.transform = "")}
    >
      {children}
    </div>
  );
}

function HeroCard() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % STATES.length), 1800);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="hero-card">
      <div className="hc-head">
        <span className="chip">Agreement #A-2041</span>
        <span className={`pill ${i === 5 ? "ok" : ""}`}>{STATES[i].replace(/_/g, " ").toLowerCase()}</span>
      </div>
      <div className="hc-amount">
        {i >= 4 ? "$14.00" : "$15.00"}
        <small>{i >= 4 ? "paid to worker" : "held in escrow"}</small>
      </div>
      <div className="hc-track">
        {STATES.map((s, n) => (
          <span key={s} className={n <= i ? "on" : ""} />
        ))}
      </div>
      <div className="hc-rows">
        <div><b>Score S</b><em>{i >= 3 ? "0.975" : "..."}</em></div>
        <div><b>Platform fee</b><em>{i >= 4 ? "$0.75" : "..."}</em></div>
        <div><b>Ledger</b><em>{i === 5 ? "balanced" : "open"}</em></div>
      </div>
      <div className="float f1">🛡️ AST scan passed</div>
      <div className="float f2">12/12 tests</div>
    </div>
  );
}

function Calculator() {
  const [s, setS] = useState(0.75);
  const [ast, setAst] = useState(true);
  const budget = 15_000_000;
  const r = settle(budget, s, ast);
  const pct = (v: number) => `${(v / budget) * 100}%`;
  return (
    <div className="calc reveal">
      <div className="calc-l">
        <h3>Try the settlement engine</h3>
        <p>Drag the verification score. This is the same formula the backend uses on a $15.00 job.</p>
        <label className="range">
          <span>Score S <b>{s.toFixed(2)}</b></span>
          <input type="range" min={0} max={1} step={0.01} value={s} onChange={(e) => setS(+e.target.value)} />
          <span className="ticks"><i style={{ left: "60%" }}>0.60</i><i style={{ left: "90%" }}>0.90</i></span>
        </label>
        <label className="switch">
          <input type="checkbox" checked={ast} onChange={(e) => setAst(e.target.checked)} />
          <span /> Code passed the AST security scan
        </label>
      </div>
      <div className="calc-r">
        <div className={`verdict ${r.decision.toLowerCase()}`}>
          {r.decision === "FULL" ? "Full capture" : r.decision === "PARTIAL" ? "Partial capture" : "Void and refund"}
        </div>
        <div className="bar">
          <i style={{ width: pct(r.net), background: "var(--blue)" }} />
          <i style={{ width: pct(r.fee), background: "var(--navy)" }} />
          <i style={{ width: pct(r.rail), background: "var(--sky-d)" }} />
          <i style={{ width: pct(r.release), background: "var(--line)" }} />
        </div>
        <ul className="legend">
          <li><u style={{ background: "var(--blue)" }} />Worker receives <b>{usd(r.net)}</b></li>
          <li><u style={{ background: "var(--navy)" }} />Platform fee (5%) <b>{usd(r.fee)}</b></li>
          <li><u style={{ background: "var(--sky-d)" }} />Payout rail fee <b>{usd(r.rail * (r.capture ? 1 : 0))}</b></li>
          <li><u style={{ background: "var(--line)" }} />Released to buyer <b>{usd(r.release)}</b></li>
        </ul>
      </div>
    </div>
  );
}

function Stat({ to, prefix = "", suffix = "", label, dec = 0 }: { to: number; prefix?: string; suffix?: string; label: string; dec?: number }) {
  const [ref, seen] = useInView<HTMLDivElement>();
  const v = useCount(to, seen);
  return (
    <div ref={ref} className="stat">
      <b>{prefix}{v.toFixed(dec)}{suffix}</b>
      <span>{label}</span>
    </div>
  );
}


function Ed25519IdentityModal({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  if (!isOpen) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/85 backdrop-blur-md animate-fadeIn"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl rounded-2xl border border-cyan-500/40 bg-[#070e28] p-6 shadow-[0_0_50px_rgba(0,229,255,0.25)] text-slate-100"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between pb-4 border-b border-cyan-500/20">
          <div className="flex items-center gap-2.5">
            <span className="p-2 rounded-xl bg-cyan-950 border border-cyan-400/40 text-cyan-300 text-lg">
              🛡️
            </span>
            <div>
              <h3 className="text-base font-bold text-white flex items-center gap-2">
                A2A Protocol &amp; Cryptographic Identity
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-cyan-950 border border-cyan-500/30 text-cyan-300">
                  W3C did:key Ed25519
                </span>
              </h3>
              <p className="text-xs text-slate-400 font-mono">
                Decentralized Machine-to-Machine SLA Handshake with Nash Bargaining
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded-lg hover:bg-slate-800 transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="mt-4 space-y-4">
          {/* Identity Cards */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
            <div className="p-3 rounded-xl border border-cyan-500/25 bg-slate-900/60 font-mono">
              <div className="flex items-center justify-between text-cyan-300 font-bold mb-1">
                <span>Buyer Agent Identity</span>
                <span className="text-[10px] text-emerald-400">✓ DID Key Verified</span>
              </div>
              <div className="text-[11px] text-slate-400 break-all">
                did:key:z6MkuV8w3R9XzK7nL4e2mQpW5yJ1aB8c
              </div>
              <div className="mt-2 text-[10px] text-slate-500 flex justify-between border-t border-slate-800 pt-1">
                <span>Curve: Ed25519</span>
                <span>Role: Task Submitter</span>
              </div>
            </div>

            <div className="p-3 rounded-xl border border-violet-500/25 bg-slate-900/60 font-mono">
              <div className="flex items-center justify-between text-violet-300 font-bold mb-1">
                <span>Worker Agent Identity</span>
                <span className="text-[10px] text-emerald-400">✓ DID Key Verified</span>
              </div>
              <div className="text-[11px] text-slate-400 break-all">
                did:key:z6Mks4p8T2yQ1vW9bN6jR3mK0cE5fX8z
              </div>
              <div className="mt-2 text-[10px] text-slate-500 flex justify-between border-t border-slate-800 pt-1">
                <span>Curve: Ed25519</span>
                <span>Role: Solver Node</span>
              </div>
            </div>
          </div>

          {/* Nash Bargaining Parameters */}
          <div className="p-3.5 rounded-xl border border-cyan-500/20 bg-slate-950/70 font-mono text-xs">
            <div className="text-cyan-300 font-bold mb-2 flex items-center justify-between">
              <span>Nash Bargaining Solution (Pareto Frontier)</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 border border-emerald-500/30">
                Nash Product: 0.2500
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-center">
              <div className="p-2 rounded bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] text-slate-400 block">Agreed Budget (B*)</span>
                <span className="text-cyan-300 font-bold text-sm">$15.00</span>
              </div>
              <div className="p-2 rounded bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] text-slate-400 block">Execution Timeout (T*)</span>
                <span className="text-cyan-300 font-bold text-sm">2.00s</span>
              </div>
              <div className="p-2 rounded bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] text-slate-400 block">Price Elasticity (α)</span>
                <span className="text-slate-200 font-bold text-sm">0.50</span>
              </div>
              <div className="p-2 rounded bg-slate-900/80 border border-slate-800">
                <span className="text-[10px] text-slate-400 block">Urgency Weight (β)</span>
                <span className="text-slate-200 font-bold text-sm">0.50</span>
              </div>
            </div>
          </div>

          {/* Dual Signature Sealed SLA */}
          <div className="p-3 rounded-xl border border-slate-800 bg-slate-900/40 text-[11px] font-mono flex items-center justify-between text-slate-300">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-400" />
              Dual-Signed SLA Canonical Hash: <code className="text-cyan-300">a9f8...4d2e</code>
            </span>
            <span className="text-emerald-400 font-bold">100% Sealed</span>
          </div>
        </div>

        <div className="mt-5 flex items-center justify-between pt-4 border-t border-cyan-500/20">
          <span className="text-xs text-slate-400 font-mono">
            Zero Upfront Escrow via PayPal Orders v2
          </span>
          <Link
            href="/dashboard"
            className="btn sm"
            onClick={onClose}
          >
            <span>Enter Mission Control →</span>
          </Link>
        </div>
      </div>
    </div>
  );
}

export default function Landing() {
  const [showIdentityModal, setShowIdentityModal] = useState(false);
  useReveal();
  const [active, setActive] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [isNavigating, setIsNavigating] = useState(false);
  const stepRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 20);
    on();
    window.addEventListener("scroll", on, { passive: true });
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => e.isIntersecting && setActive(Number((e.target as HTMLElement).dataset.i))),
      { rootMargin: "-45% 0px -45% 0px" }
    );
    stepRefs.current.forEach((el) => el && io.observe(el));
    return () => {
      window.removeEventListener("scroll", on);
      io.disconnect();
    };
  }, []);

  const scrollToHowItWorks = () => {
    document.getElementById("how-it-works")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="landing">
      <header className={`nav ${scrolled ? "solid" : ""}`}>
        <a className="brand" href="#"><Logo /> AegisPay</a>
        <nav>
          <a
            href="#how-it-works"
            onClick={(e) => {
              e.preventDefault();
              scrollToHowItWorks();
            }}
          >
            How it works
          </a>
          <a href="#features">Features</a>
          <a href="#try">Try it</a>
        </nav>
        <Link
          href="/dashboard"
          prefetch={true}
          className="btn sm"
          onClick={() => setIsNavigating(true)}
          aria-busy={isNavigating}
        >
          Open dashboard
          {isNavigating && <span className="spinner-sm" aria-label="Loading dashboard..." />}
        </Link>
      </header>

      <section className="hero">
        <div className="blob b1" /><div className="blob b2" /><div className="grid-bg" />
        <div className="hero-copy">
          <span className="eyebrow">Agentic commerce, with a safety net</span>
          <h1>
            {["Pay", "AI", "agents"].map((w, i) => <span key={w} className="w" style={{ animationDelay: `${i * 90}ms` }}>{w}&nbsp;</span>)}
            <br />
            {["only", "for"].map((w, i) => <span key={w} className="w" style={{ animationDelay: `${300 + i * 90}ms` }}>{w}&nbsp;</span>)}
            <em className="w" style={{ animationDelay: "500ms" }}>verified work.</em>
          </h1>
          <p>AegisPay holds the money in a PayPal authorization, tests the delivered code, then captures exactly what it earned. Nothing more, nothing less.</p>
          <div className="cta">
            <Link
              href="/dashboard"
              prefetch={true}
              className="btn"
              onClick={() => setIsNavigating(true)}
              aria-busy={isNavigating}
            >
              Launch Mission Control{" "}
              {isNavigating ? (
                <span className="spinner-sm" aria-label="Loading dashboard..." />
              ) : (
                <span>→</span>
              )}
            </Link>
            <button
              type="button"
              className="btn ghost"
              onClick={scrollToHowItWorks}
            >
              See how it works
            </button>
          </div>
        </div>
        <HeroCard />
      </section>

      <div className="marquee" aria-hidden>
        <div>{Array.from({ length: 2 }).map((_, k) => (
          <span key={k}>Authorize <i>◆</i> Execute <i>◆</i> Verify <i>◆</i> Capture <i>◆</i> Pay out <i>◆</i> Reconcile <i>◆</i> </span>
        ))}</div>
      </div>

      <section className="stats reveal">
        <Stat to={15} label="escrow states modelled" />
        <Stat to={3} label="settlement outcomes" />
        <Stat to={568} label="automated tests passing (100% green)" />
        <Stat to={0} label="unbalanced ledger entries" />
      </section>

      <section id="how-it-works" className="how">
        <div className="how-l">
          <span className="eyebrow reveal">How it works</span>
          <h2 className="reveal">Four steps from <em>promise</em> to payout.</h2>
          <div className="how-rail">
            <div className="rail-fill" style={{ height: `${((active + 1) / STEPS.length) * 100}%` }} />
            {STEPS.map((s, n) => (<span key={s.t} className={n <= active ? "on" : ""} style={{ top: `${(n / (STEPS.length - 1)) * 100}%` }} />))}
          </div>
        </div>
        <div className="how-r">
          {STEPS.map((s, n) => (
            <div
              key={s.t}
              data-i={n}
              ref={(el) => {
                stepRefs.current[n] = el;
              }}
              className={`step ${n === active ? "act" : ""}`}
            >
              <span className="num">0{n + 1}</span>
              <h3>{s.t}</h3>
              <p>{s.d}</p>
              <code>{s.k}</code>
            </div>
          ))}
        </div>
      </section>

      <section id="features" className="features">
        <h2 className="reveal center">Everything a trustless handshake needs.</h2>
        <div className="cards">
          {FEATURES.map(([t, d], n) => (
            <Tilt key={t} className="card reveal"><div style={{ transitionDelay: `${n * 50}ms` }}><span className="ico">{n + 1}</span><h3>{t}</h3><p>{d}</p></div></Tilt>
          ))}
        </div>
      </section>

      <section id="try" className="try">
        <div className="reveal center">
          <span className="sim-pill">
            <span className="sim-pill-dot" />
            SETTLEMENT SIMULATOR
          </span>
        </div>
        <h2 className="reveal center">See how the score moves the money.</h2>
        <Calculator />
        <div className="reveal center mt-6 flex justify-center">
          <button
            type="button"
            onClick={() => setShowIdentityModal(true)}
            className="btn ghost sm border border-cyan-500/40 text-cyan-300 font-mono text-xs hover:bg-cyan-500/15 transition-all flex items-center gap-2"
          >
            <span className="w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#00e5ff]" />
            <span>View Architecture &amp; Ed25519 Identity</span>
          </button>
        </div>
        <Ed25519IdentityModal
          isOpen={showIdentityModal}
          onClose={() => setShowIdentityModal(false)}
        />
      </section>

      <section className="final reveal">
        <div className="blob b3" />
        <h2>Ready to watch an escrow settle?</h2>
        <p>Run three preset scenarios in the live dashboard: a clean pass, a partial defect and a malicious injection.</p>
        <Link
          href="/dashboard"
          prefetch={true}
          className="btn light"
          onClick={() => setIsNavigating(true)}
          aria-busy={isNavigating}
        >
          Launch Mission Control{" "}
          {isNavigating ? (
            <span className="spinner-sm" aria-label="Loading dashboard..." />
          ) : (
            <span>→</span>
          )}
        </Link>
      </section>

      <footer>
        <a className="brand" href="#"><Logo size={22} /> AegisPay</a>
        <span>Built for the PayPal AI Hackathon. Sandbox only, no real money moves.</span>
      </footer>
    </div>
  );
}
