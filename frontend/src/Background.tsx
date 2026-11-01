import { useEffect, useRef } from "react";

/** Fixed, site-wide living background: aurora blobs, particle network, grain, cursor glow, scroll progress. */
export default function Background() {
  const cv = useRef<HTMLCanvasElement>(null);
  const glow = useRef<HTMLDivElement>(null);
  const prog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const c = cv.current!, ctx = c.getContext("2d")!;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, raf = 0;
    const m = { x: -999, y: -999 };
    type P = { x: number; y: number; vx: number; vy: number; r: number; h: number };
    let ps: P[] = [];
    const size = () => {
      const d = Math.min(devicePixelRatio || 1, 2);
      W = innerWidth; H = innerHeight;
      c.width = W * d; c.height = H * d; ctx.setTransform(d, 0, 0, d, 0, 0);
      const n = Math.min(110, Math.floor((W * H) / 14000));
      ps = Array.from({ length: n }, () => ({
        x: Math.random() * W, y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.35, vy: (Math.random() - 0.5) * 0.35,
        r: Math.random() * 1.8 + 0.6, h: Math.random() < 0.5 ? 190 : 265,
      }));
    };
    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      for (const p of ps) {
        const dx = p.x - m.x, dy = p.y - m.y, d2 = dx * dx + dy * dy;
        if (d2 < 22000) { const f = (1 - d2 / 22000) * 0.9; p.vx += (dx / 80) * f * 0.05; p.vy += (dy / 80) * f * 0.05; }
        p.vx *= 0.995; p.vy *= 0.995;
        p.x += p.vx; p.y += p.vy;
        if (p.x < -10) p.x = W + 10; if (p.x > W + 10) p.x = -10;
        if (p.y < -10) p.y = H + 10; if (p.y > H + 10) p.y = -10;
      }
      for (let i = 0; i < ps.length; i++) {
        for (let j = i + 1; j < ps.length; j++) {
          const a = ps[i], b = ps[j], dx = a.x - b.x, dy = a.y - b.y, d = dx * dx + dy * dy;
          if (d < 16000) {
            ctx.strokeStyle = `hsla(${a.h},100%,65%,${(1 - d / 16000) * 0.28})`;
            ctx.lineWidth = 0.8; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      for (const p of ps) {
        ctx.fillStyle = `hsla(${p.h},100%,70%,.9)`; ctx.shadowColor = `hsl(${p.h},100%,60%)`; ctx.shadowBlur = 10;
        ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 6.283); ctx.fill();
      }
      ctx.shadowBlur = 0;
      if (!still) raf = requestAnimationFrame(draw);
    };
    const move = (e: MouseEvent) => {
      m.x = e.clientX; m.y = e.clientY;
      glow.current?.style.setProperty("transform", `translate(${e.clientX - 260}px,${e.clientY - 260}px)`);
    };
    const scroll = () => {
      const h = document.documentElement;
      prog.current?.style.setProperty("transform", `scaleX(${h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight)})`);
    };
    size(); draw(); scroll();
    addEventListener("resize", size); addEventListener("mousemove", move); addEventListener("scroll", scroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      removeEventListener("resize", size); removeEventListener("mousemove", move); removeEventListener("scroll", scroll);
    };
  }, []);

  return (
    <>
      <div className="bg" aria-hidden>
        <i className="au a1" /><i className="au a2" /><i className="au a3" /><i className="au a4" />
        <div className="floor" />
        <canvas ref={cv} />
        <div className="grain" />
        <div className="vignette" />
      </div>
      <div className="cursor-glow" ref={glow} aria-hidden />
      <div className="progress" ref={prog} aria-hidden />
    </>
  );
}
