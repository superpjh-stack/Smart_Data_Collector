import { useEffect, useRef } from "react";
import type { ReadingPoint } from "../types";

interface Props {
  points: ReadingPoint[];
  /** A `--token` name from :root's palette (e.g. "ok", "warn", "crit"), not a raw CSS color — canvas 2D doesn't resolve var() itself. */
  colorToken: string;
}

export function Sparkline({ points, colorToken }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const color =
      getComputedStyle(document.documentElement).getPropertyValue(`--${colorToken}`).trim() || "#33b7e8";

    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    if (points.length < 2) {
      ctx.fillStyle = color;
      ctx.globalAlpha = 0.35;
      ctx.font = "10px ui-monospace, monospace";
      ctx.fillText("데이터 축적 중…", 4, h / 2 + 3);
      ctx.globalAlpha = 1;
      return;
    }

    const values = points.map((p) => p.avg_value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const range = max - min || 1;

    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = (idx / (points.length - 1)) * (w - 4) + 2;
      const y = h - 4 - ((p.avg_value - min) / range) * (h - 8);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = "round";
    ctx.stroke();

    const last = points[points.length - 1];
    const lastX = w - 2;
    const lastY = h - 4 - ((last.avg_value - min) / range) * (h - 8);
    ctx.beginPath();
    ctx.arc(lastX, lastY, 2.4, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
  }, [points, colorToken]);

  return <canvas ref={canvasRef} width={200} height={34} className="spark" />;
}
