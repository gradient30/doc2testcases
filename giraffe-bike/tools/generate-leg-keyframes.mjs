// 生成骑行腿部关键帧、链条啮合几何与"下车/上车"编排关键帧。
// 产物内联进 style.css / index.html。
// - 腿：两连杆 IK，骑行时脚在 24 个相位上精确落在曲柄圆上。
// - 链：闭环周长 = 整数链节距，齿/链相位可精确锁定。
// - 下车：身体位移与腿部髋部锚点共用同一组停点（%）和同一时序函数，
//   保证腿部画在 rider 组外（世界坐标）也与移动的身体逐帧贴合。
const fmt = (n, d = 2) => Number(n.toFixed(d));

// ---------- 骑行腿部 IK ----------
const HIP = { x: 462, y: 272 };      // 髋关节
const L1 = 95, L2 = 95;              // 大腿 / 小腿
const BB = { x: 470, y: 424 };       // 五通（曲柄中心）
const CRANK_R = 26;                  // 曲柄长
const FOOT_LIFT = 5;                 // 蹄心相对踏板轴的抬升
const TOE = [16, 4];                 // 蹄尖段
const STEPS = 24;                    // 每圈 24 帧（15°/帧）

const leg = [], hoof = [];
for (let k = 0; k <= STEPS; k++) {
  const th = (k / STEPS) * 2 * Math.PI;
  const F = { x: BB.x + CRANK_R * Math.cos(th), y: BB.y + CRANK_R * Math.sin(th) - FOOT_LIFT };
  const K = ikKnee([HIP.x, HIP.y], [F.x, F.y]);
  const T = { x: F.x + TOE[0], y: F.y + TOE[1] };
  const pct = fmt((k / STEPS) * 100);
  leg.push(`  ${pct}% { d: path("M ${HIP.x} ${HIP.y} L ${fmt(K.x)} ${fmt(K.y)} L ${fmt(F.x)} ${fmt(F.y)} L ${fmt(T.x)} ${fmt(T.y)}"); }`);
  hoof.push(`  ${pct}% { d: path("M ${fmt(F.x)} ${fmt(F.y)} L ${fmt(T.x)} ${fmt(T.y)}"); }`);
}

function ikKnee(hip, foot) {
  const dx = foot[0] - hip[0], dy = foot[1] - hip[1], d = Math.hypot(dx, dy);
  const a = (L1 * L1 - L2 * L2 + d * d) / (2 * d);
  const h = Math.sqrt(Math.max(0, L1 * L1 - a * a));
  const ux = dx / d, uy = dy / d;
  const c1 = { x: hip[0] + a * ux + h * -uy, y: hip[1] + a * uy + h * ux };
  const c2 = { x: hip[0] + a * ux - h * -uy, y: hip[1] + a * uy - h * ux };
  return c1.x >= c2.x ? c1 : c2; // 膝盖朝前
}

// ---------- 链条啮合几何 ----------
const C2 = { x: 310, y: 418 };       // 后轮心（飞轮）
const C1 = { x: 470, y: 424 };       // 五通（牙盘）
const TEETH_F = 16, TEETH_R = 8;     // 牙盘 / 飞轮齿数 → 传动比 2.0
const LINKS = 44;                    // 链条总链节数（闭合整数）
const LINK_RATIO = 0.71;             // 链片占节距比例（虚线样式）

let p = 10.0, rf = 0, rr = 0, s = 0, c = 1, top = 0, bot = 0;
for (let i = 0; i < 60; i++) {
  rf = (TEETH_F * p) / (2 * Math.PI);
  rr = (TEETH_R * p) / (2 * Math.PI);
  const dd = Math.hypot(C1.x - C2.x, C1.y - C2.y);
  s = (rf - rr) / dd; c = Math.sqrt(1 - s * s);
  top = Math.hypot(dd + (rf - rr) * s, (C1.y - C2.y) - (rf - rr) * c);
  bot = Math.hypot(dd - (rf - rr) * s, (C1.y - C2.y) + (rf - rr) * c);
  p = (top + bot) / (LINKS - (TEETH_F + TEETH_R) / 2);
}
const phi = (Math.asin(s) * 180) / Math.PI;
const D = fmt(p * LINK_RATIO, 3), G = fmt(p * (1 - LINK_RATIO), 3);
const phase0 = fmt(D + G / 2, 3);
const phaseEnd = fmt(D + G / 2 - TEETH_F * p, 3);

const topCog = [fmt(C2.x + rr * s), fmt(C2.y - rr * c)];
const topRing = [fmt(C1.x + rf * s), fmt(C1.y - rf * c)];
const botRing = [fmt(C1.x - rf * s), fmt(C1.y + rf * c)];
const botCog = [fmt(C2.x - rr * s), fmt(C2.y + rr * c)];
const pt = q => `${q[0]} ${q[1]}`;
const chainD = `M ${pt(topCog)} L ${pt(topRing)} A ${fmt(rf)} ${fmt(rf)} 0 1 1 ${pt(botRing)} L ${pt(botCog)} A ${fmt(rr)} ${fmt(rr)} 0 1 1 ${pt(topCog)} Z`;

const k0 = leg[0].match(/path\("([^"]+)"\)/)[1];
const h0 = hoof[0].match(/path\("([^"]+)"\)/)[1];

// ---------- 下车编排 ----------
// 身体位移轨迹（% 停点 → [dx, dy]，世界坐标下髋部 = 髋基点 + 位移）
const RIDER_STOPS = [0, 12, 45, 75, 100];
const RIDER_SHIFT = [[0, 0], [6, -8], [-30, -6], [-72, 0], [-88, 2]];
// 腿部关键帧的统一停点（腿部/rider 共用，保证逐帧贴合）
const SHIFT_STOPS = [0, 10, 12, 25, 28, 40, 45, 55, 62, 70, 75, 78, 80, 90, 100];

const shiftAt = q => {
  for (let i = 1; i < RIDER_STOPS.length; i++) {
    if (q <= RIDER_STOPS[i]) {
      const t = (q - RIDER_STOPS[i - 1]) / (RIDER_STOPS[i] - RIDER_STOPS[i - 1]);
      return [RIDER_SHIFT[i - 1][0] + t * (RIDER_SHIFT[i][0] - RIDER_SHIFT[i - 1][0]),
              RIDER_SHIFT[i - 1][1] + t * (RIDER_SHIFT[i][1] - RIDER_SHIFT[i - 1][1])];
    }
  }
  return RIDER_SHIFT[RIDER_SHIFT.length - 1];
};
const footAt = (way, q) => {
  for (let i = 1; i < way.length; i++) {
    if (q <= way[i][0]) {
      const t = (q - way[i - 1][0]) / (way[i][0] - way[i - 1][0]);
      const a = way[i - 1][1], b = way[i][1];
      return [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])];
    }
  }
  return way[way.length - 1][1];
};

const P0 = [BB.x + CRANK_R, BB.y - FOOT_LIFT];    // 相位 0°踏点（下车规范相位）
const P180 = [BB.x - CRANK_R, BB.y - FOOT_LIFT];  // 相位 180°踏点（远腿）
// 近腿：前踏点 → 抬起 → 向左跨过车架（近腿在车架前侧 z 层）→ 落地
const NEAR_WAY = [[0, P0], [10, [510, 398]], [28, [494, 368]], [45, [444, 362]], [62, [400, 378]],
                  [78, [372, 420]], [90, [358, 448]], [100, [356, 463]]];
// 远腿：后踏点 → 先下地站稳
const FAR_WAY = [[0, P180], [10, [452, 404]], [25, [462, 376]], [40, [420, 372]], [55, [398, 400]],
                 [70, [392, 440]], [78, [392, 461]], [100, [390, 463]]];

function dismountTrack(way) {
  const legKf = [], hoofKf = [];
  for (const q of SHIFT_STOPS) {
    const s = shiftAt(q);
    const hip = [HIP.x + s[0], HIP.y + s[1]];
    const F = footAt(way, q);
    const K = ikKnee(hip, F);
    const T = [F[0] + TOE[0], F[1] + TOE[1]];
    legKf.push(`  ${q}% { d: path("M ${fmt(hip[0])} ${fmt(hip[1])} L ${fmt(K.x)} ${fmt(K.y)} L ${fmt(F[0])} ${fmt(F[1])} L ${fmt(T[0])} ${fmt(T[1])}"); }`);
    hoofKf.push(`  ${q}% { d: path("M ${fmt(F[0])} ${fmt(F[1])} L ${fmt(T[0])} ${fmt(T[1])}"); }`);
  }
  return { legKf, hoofKf };
}
const near = dismountTrack(NEAR_WAY);
const far = dismountTrack(FAR_WAY);

// 前臂（扶把 → 松把垂下）：肩点 = 肩基点 + 身体位移（与 rider 同停点同时序 → 逐帧贴合）
const ARM_STOPS = RIDER_STOPS;
const ARM_N = [
  [[546, 234], [558, 262], [592, 292], [608, 297]],
  [[552, 226], [560, 258], [592, 290], [608, 295]],
  [[516, 228], [505, 258], [512, 288], [528, 293]],
  [[474, 234], [464, 264], [468, 298], [484, 303]],
  [[458, 236], [452, 272], [462, 308], [478, 313]],
];
const ARM_F = [
  [[538, 238], [548, 264], [584, 292], [600, 297]],
  [[544, 230], [552, 260], [584, 290], [600, 295]],
  [[508, 232], [496, 260], [504, 288], [520, 293]],
  [[466, 238], [456, 264], [460, 296], [476, 301]],
  [[450, 240], [442, 272], [450, 308], [466, 313]],
];
function armTrack(arm) {
  const kf = [], hoofKf = [];
  ARM_STOPS.forEach((q, i) => {
    const [S, E, F, T] = arm[i];
    kf.push(`  ${q}% { d: path("M ${S[0]} ${S[1]} L ${E[0]} ${E[1]} L ${F[0]} ${F[1]} L ${T[0]} ${T[1]}"); }`);
    hoofKf.push(`  ${q}% { d: path("M ${F[0]} ${F[1]} L ${T[0]} ${T[1]}"); }`);
  });
  return { kf, hoofKf };
}
const armN = armTrack(ARM_N);
const armF = armTrack(ARM_F);

const riderKf = SHIFT_STOPS.map(q => {
  const s = shiftAt(q);
  return `  ${q}% { transform: translate(${fmt(s[0])}px, ${fmt(s[1])}px); }`;
});

console.log(`/* ===== 由 tools/generate-leg-keyframes.mjs 生成 ===== */
/* 腿部踏频关键帧：脚全程落在曲柄圆上，周期与曲柄同为 4s */
@keyframes pedalStroke {
${leg.join("\n")}
}

@keyframes hoofStroke {
${hoof.join("\n")}
}

/* 下车编排：0% 帧与骑行规范相位（0°/180°）逐像素一致，交换动画无跳变；
   上车直接复用同一组关键帧反向播放（animation-direction: reverse） */
@keyframes dismountRider {
${riderKf.join("\n")}
}

@keyframes dismountLegNear {
${near.legKf.join("\n")}
}

@keyframes dismountHoofNear {
${near.hoofKf.join("\n")}
}

@keyframes dismountLegFar {
${far.legKf.join("\n")}
}

@keyframes dismountHoofFar {
${far.hoofKf.join("\n")}
}

@keyframes dismountArmNear {
${armN.kf.join("\n")}
}

@keyframes dismountArmHoofNear {
${armN.hoofKf.join("\n")}
}

@keyframes dismountArmFar {
${armF.kf.join("\n")}
}

@keyframes dismountArmHoofFar {
${armF.hoofKf.join("\n")}
}

/* 链条：闭环 ${LINKS} 节，节距 p=${fmt(p, 4)}px，牙盘 ${TEETH_F} 齿/飞轮 ${TEETH_R} 齿
   每曲柄一圈链条前进 ${TEETH_F}×p=${fmt(TEETH_F * p, 3)}px = 16 个虚线周期 → 相位精确锁定 */
@keyframes chainFlow {
  from { stroke-dashoffset: ${phase0}px; }
  to   { stroke-dashoffset: ${phaseEnd}px; }
}
===== SVG 数值（当前已内联，供重建参考） =====
chainD = "${chainD}"
dasharray = "${D} ${G}"
rf=${fmt(rf)} rr=${fmt(rr)} phi=${fmt(phi)}deg
legD0  = "${k0}"
hoofD0 = "${h0}"`);
