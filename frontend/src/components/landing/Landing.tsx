"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
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

export default function Landing() {
  const router = useRouter();
  useReveal();
  const [active, setActive] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const [isLaunching, setIsLaunching] = useState(false);
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

  const go = () => router.push("/dashboard");
  const handleLaunch = () => {
    setIsLaunching(true);
    router.push("/dashboard");
  };

  return (
    <div className="landing">
      <header className={`nav ${scrolled ? "solid" : ""}`}>
        <a className="brand" href="#"><Logo /> AegisPay</a>
        <nav>
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#try">Try it</a>
        </nav>
        <button className="btn sm" onClick={go}>Open dashboard</button>
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
            <button className="btn" onClick={go}>Get started <span>→</span></button>
            <a className="btn ghost" href="#how">See how it works</a>
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
        <Stat to={539} label="automated tests passing" />
        <Stat to={0} label="unbalanced ledger entries" />
      </section>

      <section id="how" className="how">
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
      </section>

      <section className="final reveal">
        <div className="blob b3" />
        <h2>Ready to watch an escrow settle?</h2>
        <p>Run three preset scenarios in the live dashboard: a clean pass, a partial defect and a malicious injection.</p>
        <button
          className="btn light"
          onClick={handleLaunch}
          disabled={isLaunching}
          aria-busy={isLaunching}
        >
          Launch Mission Control{" "}
          {isLaunching ? (
            <span className="spinner-sm" aria-label="Loading dashboard..." />
          ) : (
            <span>→</span>
          )}
        </button>
      </section>

      <footer>
        <a className="brand" href="#"><Logo size={22} /> AegisPay</a>
        <span>Built for the PayPal AI Hackathon. Sandbox only, no real money moves.</span>
      </footer>
    </div>
  );
}
