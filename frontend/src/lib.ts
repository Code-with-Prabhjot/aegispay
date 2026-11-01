import { useEffect, useRef, useState } from "react";

export const usd = (micros: number) =>
  "$" + (micros / 1_000_000).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Adds `.in` to elements with `.reveal` once they scroll into view. */
export function useReveal() {
  useEffect(() => {
    const io = new IntersectionObserver(
      (es) => es.forEach((e) => e.isIntersecting && (e.target.classList.add("in"), io.unobserve(e.target))),
      { threshold: 0.15 }
    );
    document.querySelectorAll(".reveal").forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);
}

/** Animated count-up. */
export function useCount(target: number, run: boolean, ms = 900) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (!run) return;
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const p = Math.min(1, (t - t0) / ms);
      setV(target * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, run, ms]);
  return v;
}

export function useInView<T extends Element>() {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (!ref.current) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && (setSeen(true), io.disconnect()), { threshold: 0.3 });
    io.observe(ref.current);
    return () => io.disconnect();
  }, []);
  return [ref, seen] as const;
}

/** Mirrors the settlement formula in backend paypal_escrow.compute_settlement_split_micros (estimate). */
export function settle(budgetMicros: number, s: number, astOk: boolean) {
  if (!astOk || s < 0.6) return { decision: "VOID", capture: 0, release: budgetMicros, fee: 0, rail: 0, net: 0 };
  let capture = budgetMicros;
  let decision = "FULL";
  if (s < 0.9) {
    const f = 0.5 + 0.4 * ((s - 0.6) / 0.3);
    capture = Math.floor((budgetMicros * f) / 10_000) * 10_000;
    decision = "PARTIAL";
  }
  const fee = Math.floor((capture * 0.05) / 10_000) * 10_000;
  const rail = 250_000;
  return { decision, capture, release: budgetMicros - capture, fee, rail, net: Math.max(0, capture - fee - rail) };
}
