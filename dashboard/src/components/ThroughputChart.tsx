import { useEffect, useRef } from "react";
import type { ThroughputPoint } from "../types";

interface Props {
  points: ThroughputPoint[];
  color: string;
}

export function ThroughputChart({ points, color }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);

    if (points.length < 2) return;

    const values = points.map((p) => p.reading_count);
    const max = Math.max(...values) * 1.15 || 1;

    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    for (let g = 1; g < 4; g++) {
      const gy = ((h - 6) * g) / 4 + 3;
      ctx.beginPath();
      ctx.moveTo(0, gy);
      ctx.lineTo(w, gy);
      ctx.stroke();
    }

    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, hexToRgba(color, 0.28));
    grad.addColorStop(1, hexToRgba(color, 0.02));

    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = (idx / (points.length - 1)) * w;
      const y = h - 4 - (p.reading_count / max) * (h - 10);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = (idx / (points.length - 1)) * w;
      const y = h - 4 - (p.reading_count / max) * (h - 10);
      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.8;
    ctx.stroke();
  }, [points, color]);

  return <canvas ref={canvasRef} width={1160} height={74} />;
}

function hexToRgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const parts =
    h.length === 3
      ? h.split("").map((c) => c + c)
      : [h.slice(0, 2), h.slice(2, 4), h.slice(4, 6)];
  const [r, g, b] = parts.map((p) => parseInt(p, 16));
  return `rgba(${r},${g},${b},${alpha})`;
}
