import { useEffect, useRef } from "react";

/**
 * 声音标本 — 把一场会议画成一件昆虫图版式的标本。
 *
 * 蝴蝶图版收藏的是易逝之物:活过、飞过,然后被制成可归档的标本。
 * 会议是同一回事——话说完就散了,筑听把它做成标本。
 *
 * 解剖参照真实鳞翅目展翅标本:身体纵置,前翅向上外展、后翅向下圆收。
 * 形态由会议数据确定性生成,同一场会议永远得到同一只标本:
 *   翅缘起伏 ← 语音包络    翅脉数 ← 发言密度
 *   斑纹分布 ← 会议指纹    体节数 ← 发言片段
 *   颜料     ← 会议场景    体量   ← 会议时长
 */

export type SpecimenScene = "talk" | "general" | "personal" | "interview" | "customer_visit" | "auto";

type Props = {
  seed: string;
  scene?: SpecimenScene;
  duration?: number;
  className?: string;
  label?: string;
  caption?: string;
};

/** 场景颜料 — 参照图版:近黑翅底 + 一种浓色条带 + 缘斑
 *  ground 是翅底(几乎全是深色),bar 是硬边色带,dot 是缘斑 */
const PIGMENTS: Record<SpecimenScene, { ground: string; bar: string; bar2: string; dot: string; body: string }> = {
  talk:           { ground: "#24302D", bar: "#6A9B8D", bar2: "#B7C78B", dot: "#E6D79A", body: "#A84B32" },
  general:        { ground: "#29291D", bar: "#A5A158", bar2: "#D4BC64", dot: "#EEE0A4", body: "#A94B2E" },
  customer_visit: { ground: "#30271C", bar: "#C3973D", bar2: "#E4C568", dot: "#F1DFA6", body: "#A9482D" },
  interview:      { ground: "#311B18", bar: "#BB5A38", bar2: "#DB9A55", dot: "#E9D28E", body: "#B43C2D" },
  personal:       { ground: "#29232B", bar: "#81728D", bar2: "#B69A73", dot: "#E7D59B", body: "#A34A37" },
  auto:           { ground: "#2C291F", bar: "#B38E3E", bar2: "#D9BB5A", dot: "#EEE0A4", body: "#AB4930" },
};

function hashSeed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h) || 1;
}

function rng(seed: number) {
  let s = seed % 2147483647;
  if (s <= 0) s += 2147483646;
  return () => {
    s = (s * 16807) % 2147483647;
    return (s - 1) / 2147483646;
  };
}

export function SoundSpecimen({ seed, scene = "auto", duration = 3600, className, label, caption }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const W = canvas.clientWidth || 380;
    const H = canvas.clientHeight || 200;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const rand = rng(hashSeed(seed));
    const pig = PIGMENTS[scene] || PIGMENTS.auto;
    const INK = "#241B16";

    // 体量:10 分钟 → 3 小时
    const bulk = 0.86 + Math.min(1, Math.max(0, (duration - 600) / 9000)) * 0.24;
    const S = Math.min(W * 0.43, H * 0.56) * bulk;   // 单位尺度 = 半翅展
    const cx = W / 2;
    const cy = H * 0.50;

    // 图版纸张不是纯白。透明颗粒只画在画布里，不影响外层版式。
    ctx.save();
    for (let i = 0; i < Math.max(36, Math.floor((W * H) / 1700)); i += 1) {
      ctx.globalAlpha = 0.025 + rand() * 0.035;
      ctx.fillStyle = rand() > 0.35 ? "#6E5139" : "#C39A64";
      ctx.beginPath();
      ctx.arc(rand() * W, rand() * H, 0.25 + rand() * 0.75, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();

    /* 语音包络:每场会议独有的声音指纹,用于扰动翅缘 */
    const harm = [
      { f: 1, a: 1.0, p: rand() * 6.28 },
      { f: 2, a: 0.26 + rand() * 0.2, p: rand() * 6.28 },
      { f: 3.5, a: 0.13 + rand() * 0.14, p: rand() * 6.28 },
    ];
    const env = (t: number) => {
      let v = 0;
      for (const h of harm) v += h.a * Math.sin(Math.PI * t * h.f + h.p);
      return 1 + v * 0.055;   // 只做轻微扰动,保住翅形的美感
    };

    /* ── 翅型:归一化解剖轮廓(右半侧,x 向外、y 向上为正) ──
       前翅:自胸部沿前缘外扬至翅顶,再由外缘收回臀角
       后翅:自胸部向下外张,圆转收回                       */
    type P = [number, number];
    const FORE: P[] = [
      [0.04, 0.08],
      [0.22, 0.56], [0.61, 0.98], [1.02, 0.91],   // 蛾类细长、略尖的前翅
      [1.09, 0.70], [0.95, 0.42], [0.66, 0.22],
      [0.42, 0.08], [0.18, -0.01], [0.04, 0.08],
    ];
    const HIND: P[] = [
      [0.03, 0.03],
      [0.25, -0.08], [0.50, -0.14], [0.68, -0.32],
      [0.73, -0.49], [0.55, -0.66], [0.35, -0.61],
      [0.17, -0.54], [0.07, -0.27], [0.03, 0.03],
    ];

    const drawWing = (shape: P[], dir: 1 | -1, sx: number, sy: number, isFore: boolean) => {
      const X = (p: P, k = 1) => cx + dir * p[0] * S * sx * k;
      const Y = (p: P, k = 1) => cy - p[1] * S * sy * k;

      ctx.save();
      // 轻微不对称:手绘感
      const tilt = (dir === 1 ? 1 : -1) * (rand() - 0.5) * 0.035;
      ctx.translate(cx, cy);
      ctx.rotate(tilt);
      ctx.translate(-cx, -cy);

      const path = new Path2D();
      path.moveTo(X(shape[0]), Y(shape[0]));
      for (let i = 1; i + 2 < shape.length; i += 3) {
        const c1 = shape[i], c2 = shape[i + 1], e = shape[i + 2];
        const k = env((i + 1) / shape.length);
        path.bezierCurveTo(X(c1, k), Y(c1, k), X(c2, k), Y(c2, k), X(e, k), Y(e, k));
      }
      path.closePath();

      // 翅底:近黑实色(图版蝴蝶的主调就是深色底)
      ctx.fillStyle = pig.ground;
      ctx.fill(path);

      ctx.save();
      ctx.clip(path);

      // ── 矿物颜料色窗:旧图版常见的扇形黄斑，不追求机器式等宽 ──
      const nBar = isFore ? 6 : 4;
      for (let i = 0; i < nBar; i++) {
        const t = (i + 0.5) / nBar;
        const tip: P = isFore
          ? [0.24 + t * 0.80, 0.10 + Math.sin(Math.PI * t * 0.78) * 0.80]
          : [0.14 + t * 0.66, -0.14 - Math.sin(Math.PI * t * 0.72) * 0.62];
        const inner = 0.31 + (1 - Math.sin(Math.PI * t)) * 0.10;
        const outer = 0.77 + env(t) * 0.055;
        const half = (isFore ? 0.055 : 0.062) * (0.78 + Math.sin(Math.PI * t) * 0.42);
        const dx = tip[0], dy = tip[1];
        const len = Math.hypot(dx, dy) || 1;
        const nx = -dy / len * half;
        const ny = dx / len * half;
        ctx.fillStyle = i % 3 === 1 ? pig.bar2 : pig.bar;
        ctx.globalAlpha = 0.88 + rand() * 0.08;
        ctx.beginPath();
        ctx.moveTo(X([dx * inner + nx * 0.55, dy * inner + ny * 0.55]), Y([dx * inner + nx * 0.55, dy * inner + ny * 0.55]));
        ctx.lineTo(X([dx * outer + nx, dy * outer + ny]), Y([dx * outer + nx, dy * outer + ny]));
        ctx.quadraticCurveTo(X([dx * (outer + 0.04), dy * outer]), Y([dx * (outer + 0.04), dy * outer]), X([dx * outer - nx, dy * outer - ny]), Y([dx * outer - nx, dy * outer - ny]));
        ctx.lineTo(X([dx * inner - nx * 0.55, dy * inner - ny * 0.55]), Y([dx * inner - nx * 0.55, dy * inner - ny * 0.55]));
        ctx.closePath();
        ctx.fill();
      }

      // 翅脉保留干笔断续感。
      ctx.globalAlpha = 0.62;
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.05 * bulk;
      const nv = isFore ? 7 : 5;
      for (let i = 1; i <= nv; i++) {
        const t = i / (nv + 1);
        const tip: P = isFore
          ? [0.28 + t * 0.76, 0.12 + Math.sin(Math.PI * t * 0.8) * 0.80]
          : [0.16 + t * 0.64, -0.16 - Math.sin(Math.PI * t * 0.7) * 0.62];
        ctx.beginPath();
        ctx.moveTo(cx + dir * S * 0.03, cy);
        ctx.quadraticCurveTo(X([tip[0] * 0.46, tip[1] * 0.42]), Y([tip[0] * 0.46, tip[1] * 0.42]), X(tip), Y(tip));
        ctx.stroke();
      }

      // 缘斑:图版中的少量浅色识别点。
      ctx.globalAlpha = 0.82;
      const ns = isFore ? 5 : 4;
      for (let i = 0; i < ns; i++) {
        const t = (i + 0.5) / ns;
        const p: P = isFore
          ? [(0.26 + t * 0.76) * 0.94, (0.12 + Math.sin(Math.PI * t * 0.78) * 0.80) * 0.94]
          : [(0.16 + t * 0.64) * 0.92, (-0.16 - Math.sin(Math.PI * t * 0.72) * 0.62) * 0.92];
        ctx.beginPath();
        ctx.ellipse(X(p), Y(p), (2.2 + rand() * 1.5) * bulk, (1.2 + rand()) * bulk, (rand() - 0.5) * 0.4, 0, Math.PI * 2);
        ctx.fillStyle = pig.dot;
        ctx.fill();
      }

      // 水彩感:极淡的斑驳,避免纯矢量的呆板
      ctx.globalAlpha = 0.075;
      for (let i = 0; i < 34; i++) {
        const p: P = [rand() * 1.05, (isFore ? 1 : -1) * rand() * 0.85];
        ctx.beginPath();
        ctx.arc(X(p), Y(p), (3 + rand() * 9) * bulk, 0, Math.PI * 2);
        ctx.fillStyle = rand() > 0.5 ? "#FFFFFF" : "#000000";
        ctx.fill();
      }
      ctx.restore();

      // 轮廓
      ctx.globalAlpha = 0.74;
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.15;
      ctx.lineJoin = "round";
      ctx.stroke(path);
      // 第二遍略错位的墨线，模拟旧版套色与手工描边。
      ctx.globalAlpha = 0.22;
      ctx.translate(dir * 0.7, -0.35);
      ctx.stroke(path);
      ctx.restore();
    };

    // 纸面淡影
    ctx.save();
    ctx.globalAlpha = 0.07;
    ctx.filter = "blur(7px)";
    ctx.fillStyle = INK;
    ctx.beginPath();
    ctx.ellipse(cx, cy + S * 0.5, S * 1.0, S * 0.30, 0, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();

    // 后翅在下,前翅在上
    drawWing(HIND, -1, 1, 1, false);
    drawWing(HIND, 1, 1, 1, false);
    drawWing(FORE, -1, 1, 1, true);
    drawWing(FORE, 1, 1, 1, true);

    /* ── 身体:纵置,胸 + 分节腹部 + 头 ── */
    const bodyTop = cy - S * 0.30;
    const bodyBot = cy + S * 0.60;
    const bw = S * 0.072 * bulk;   // 图版里的腹部相当粗壮

    // 足画在身体下方。细长、略不对称，比规则图标更接近标本画。
    ctx.save();
    ctx.strokeStyle = INK;
    ctx.globalAlpha = 0.72;
    ctx.lineWidth = 1.15 * bulk;
    ctx.lineCap = "round";
    ([-1, 1] as const).forEach(d => {
      const jitter = () => (rand() - 0.5) * S * 0.045;
      [
        { y: -0.10, x1: 0.34, x2: 0.58, ey: -0.28 },
        { y: 0.13, x1: 0.31, x2: 0.55, ey: 0.38 },
      ].forEach(leg => {
        ctx.beginPath();
        ctx.moveTo(cx + d * bw * 0.75, cy + S * leg.y);
        ctx.quadraticCurveTo(cx + d * S * leg.x1, cy + S * (leg.y + jitter() / S), cx + d * S * leg.x2, cy + S * leg.ey + jitter());
        ctx.stroke();
      });
    });
    ctx.restore();

    // 腹部环节 = 发言片段；赤红、赭黄与暗墨交替成环。
    const segs = 8;
    const segH = (bodyBot - cy) / segs;
    for (let i = 0; i < segs; i++) {
      const t = i / segs;
      const y = cy + t * (bodyBot - cy) + segH * 0.5;
      const w = bw * (1 - t * 0.46);
      ctx.beginPath();
      ctx.ellipse(cx, y, w, segH * 0.66, 0, 0, Math.PI * 2);
      ctx.fillStyle = i % 3 === 0 ? pig.ground : (i % 2 === 0 ? pig.bar : pig.body);
      ctx.fill();
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = INK;
      ctx.lineWidth = 0.7;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }
    // 胸:最粗的一段
    ctx.beginPath();
    ctx.ellipse(cx, cy - S * 0.04, bw * 1.28, S * 0.19, 0, 0, Math.PI * 2);
    ctx.fillStyle = pig.body;
    ctx.fill();
    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = INK;
    ctx.lineWidth = 0.8;
    ctx.stroke();
    ctx.globalAlpha = 1;
    // 头 + 复眼
    ctx.beginPath();
    ctx.arc(cx, bodyTop, bw * 1.02, 0, Math.PI * 2);
    ctx.fillStyle = INK;
    ctx.fill();
    ([-1, 1] as const).forEach(d => {
      ctx.beginPath();
      ctx.arc(cx + d * bw * 0.62, bodyTop, bw * 0.42, 0, Math.PI * 2);
      ctx.fillStyle = INK;
      ctx.fill();
    });

    /* ── 触须:细长、外张、末端渐粗,图版里常相互交错 ── */
    ctx.globalAlpha = 0.88;
    ([-1, 1] as const).forEach(d => {
      const ex = cx + d * S * 0.46;
      const ey = bodyTop - S * 0.44;
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.5 * bulk;
      ctx.beginPath();
      ctx.moveTo(cx + d * bw * 0.4, bodyTop);
      ctx.bezierCurveTo(
        cx + d * S * 0.06, bodyTop - S * 0.26,
        cx + d * S * 0.26, ey + S * 0.16,
        ex, ey,
      );
      ctx.stroke();
      // 末端棒
      ctx.lineWidth = 3.2 * bulk;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.moveTo(ex - d * S * 0.035, ey + S * 0.028);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.lineCap = "butt";
    });
    ctx.globalAlpha = 1;

    // 自然史图版的微型编号，来自会议指纹，不引入随机变化。
    const plateNo = (hashSeed(seed) % 89) + 1;
    ctx.save();
    ctx.fillStyle = "#6F5D4B";
    ctx.globalAlpha = 0.56;
    ctx.font = `${Math.max(8, Math.min(11, W / 42))}px Georgia, serif`;
    ctx.textAlign = "center";
    ctx.fillText(String(plateNo), cx + S * 0.94, cy + S * 0.72);
    ctx.restore();
    };

    draw();

    // 容器宽度变化(窗口缩放、手机旋转、侧栏开合)后必须按新尺寸重绘,
    // 否则位图仍是旧宽度、被拉伸后发虚。用 rAF 合并连续触发,避免拖动时反复重画。
    let pending = 0;
    const observer = new ResizeObserver(() => {
      window.cancelAnimationFrame(pending);
      pending = window.requestAnimationFrame(draw);
    });
    observer.observe(canvas);
    return () => {
      window.cancelAnimationFrame(pending);
      observer.disconnect();
    };
  }, [seed, scene, duration]);

  return (
    <figure className={`specimen ${className || ""}`}>
      <canvas ref={ref} className="specimen-canvas" aria-label={label ? `声音标本:${label}` : "声音标本"} />
      {(label || caption) && (
        <figcaption className="specimen-cap">
          {label && <b>{label}</b>}
          {caption && <small>{caption}</small>}
        </figcaption>
      )}
    </figure>
  );
}

export default SoundSpecimen;
