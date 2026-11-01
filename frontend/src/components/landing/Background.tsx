"use client";

import { useEffect, useRef } from "react";

/** Fixed, site-wide living background: aurora blobs, particle network, grain, cursor glow, scroll progress. */
export default function Background() {
  const cv = useRef<HTMLCanvasElement>(null);
  const glow = useRef<HTMLDivElement>(null);
  const prog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    if (!ctx) return;
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let W = 0, H = 0, raf = 0;
    const m = { x: -999, y: -999 };
    type P = { x: number; y: number; vx: number; vy: number; r: number; h: number };
    let ps: P[] = [];
    const size = () => {
      const d = Math.min(devicePixelRatio || 1, 1.5);
      W = window.innerWidth;
      H = window.innerHeight;
      c.width = W * d;
      c.height = H * d;
      ctx.setTransform(d, 0, 0, d, 0, 0);
      const n = Math.min(60, Math.floor((W * H) / 24000));
      ps = Array.from({ length: n }, () => ({
        x: Math.random() * W,
        y: Math.random() * H,
        vx: (Math.random() - 0.5) * 0.35,
        vy: (Math.random() - 0.5) * 0.35,
        r: Math.random() * 1.8 + 0.6,
        h: Math.random() < 0.5 ? 190 : 265,
      }));
    };
    const draw = () => {
      ctx.clearRect(0, 0, W, H);
      for (const p of ps) {
        const dx = p.x - m.x, dy = p.y - m.y, d2 = dx * dx + dy * dy;
        if (d2 < 22000) {
          const f = (1 - d2 / 22000) * 0.9;
          p.vx += (dx / 80) * f * 0.05;
          p.vy += (dy / 80) * f * 0.05;
        }
        p.vx *= 0.995;
        p.vy *= 0.995;
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < -10) p.x = W + 10;
        if (p.x > W + 10) p.x = -10;
        if (p.y < -10) p.y = H + 10;
        if (p.y > H + 10) p.y = -10;
      }
      for (let i = 0; i < ps.length; i++) {
        for (let j = i + 1; j < ps.length; j++) {
          const a = ps[i], b = ps[j], dx = a.x - b.x, dy = a.y - b.y, d = dx * dx + dy * dy;
          if (d < 14000) {
            ctx.strokeStyle = `hsla(${a.h},100%,65%,${(1 - d / 14000) * 0.28})`;
            ctx.lineWidth = 0.8;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      for (const p of ps) {
        ctx.fillStyle = `hsla(${p.h},100%,70%,.9)`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, 6.283);
        ctx.fill();
      }
      if (!still && !document.hidden) raf = requestAnimationFrame(draw);
    };
    const move = (e: MouseEvent) => {
      m.x = e.clientX;
      m.y = e.clientY;
      glow.current?.style.setProperty("transform", `translate(${e.clientX - 260}px,${e.clientY - 260}px)`);
    };
    let tick = false;
    const scroll = () => {
      if (tick) return;
      tick = true;
      requestAnimationFrame(() => {
        tick = false;
        upd();
      });
    };
    const upd = () => {
      const h = document.documentElement;
      prog.current?.style.setProperty("transform", `scaleX(${h.scrollTop / Math.max(1, h.scrollHeight - h.clientHeight)})`);
    };
    const vis = () => {
      if (!document.hidden && !still) {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(draw);
      }
    };
    document.addEventListener("visibilitychange", vis);
    size();
    draw();
    upd();
    window.addEventListener("resize", size);
    window.addEventListener("mousemove", move);
    window.addEventListener("scroll", scroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener("visibilitychange", vis);
      window.removeEventListener("resize", size);
      window.removeEventListener("mousemove", move);
      window.removeEventListener("scroll", scroll);
    };
  }, []);

  return (
    <>
      <div className="bg" aria-hidden>
        <i className="au a1" />
        <i className="au a2" />
        <i className="au a3" />
        <i className="au a4" />
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
