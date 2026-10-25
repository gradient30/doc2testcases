/* JS 职责边界：只调参数、只切状态、只读数。
   - 调速：document.getAnimations() 统一写 playbackRate（同步生效，currentTime 连续无相位跳变）；
   - 停车：把倍率设为"恰好滑行到曲柄规范相位 0°"的值，rAF 里只读 currentTime 检测过零，
     到达后切 .parked 类 —— 之后全部由 CSS 关键帧接管（下车、放下支架、驻车呼吸）；
   - 上车：切 .mounting 类，CSS 反向播放同一组关键帧（收支架→回位→踩上踏板），
     animationend 通知 JS 摘类，骑行循环动画从规范相位 0 无缝继续；
   - 读数：以动画 currentTime 为唯一时间源换算车速/踏频/里程/相位差。
   动画本身 100% 由 CSS @keyframes 驱动，JS 不写任何 transform/dashoffset。 */
(() => {
  'use strict';

  // 物理标定：1px = 1cm → 车轮半径 52px = 0.52m
  const WHEEL_PERIOD_MS = 2000;                          // 车轮 2s/圈
  const CRANK_PERIOD_MS = 4000;                          // 曲柄 4s/圈
  const WHEEL_CIRC_M = 2 * Math.PI * 0.52;               // 3.2673 m
  const BASE_KMH = (WHEEL_CIRC_M / (WHEEL_PERIOD_MS / 1000)) * 3.6; // 5.881 km/h @ ×1

  const $ = id => document.getElementById(id);
  const svg = $('scene');
  const rateInput = $('rate'), rateVal = $('rateVal'), toggleBtn = $('toggle');
  const kmhEl = $('kmh'), cadEl = $('cad'), wrpmEl = $('wrpm'), distEl = $('dist');
  const phaseEl = $('phase'), lockBadge = $('lockBadge'), stateTxt = $('stateTxt');

  let anims = [], rearWheel = null, crank = null;
  let state = 'riding';        // riding | stopping | parked | mounting
  let prevAng = null;          // 停车滑行时上一帧的曲柄角

  function collect() {
    anims = document.getAnimations();
    const find = (name, sel) => anims.find(a =>
      a.animationName === name && a.effect && a.effect.target && a.effect.target.matches(sel));
    rearWheel = find('spinFull', '#wheelRear');
    crank = find('spinFull', '#crankMaster');
  }

  function applyRate(v) {
    // 同步设置 playbackRate：currentTime 连续、无相位跳变；
    // 所有动画共用同一倍率，关键帧周期整数比关系不变 → 齿轮相位保持锁定。
    for (const a of anims) a.playbackRate = v;
    rateVal.textContent = '×' + v.toFixed(2);
  }

  const LABELS = {
    riding:   ['⏸ 停车', '骑行中'],
    stopping: ['⋯',     '减速滑行至下车点…'],
    parked:   ['▶ 骑行', '已驻车 · 支架已放下'],
    mounting: ['⋯',     '上车中…'],
  };
  function setUI() {
    const busy = state === 'stopping' || state === 'mounting';
    toggleBtn.textContent = LABELS[state][0];
    toggleBtn.disabled = busy;
    stateTxt.textContent = LABELS[state][1];
    rateInput.disabled = state !== 'riding';   // 非骑行态倍率由停车逻辑接管
    rateInput.style.opacity = rateInput.disabled ? .45 : 1;
  }

  function toggle() {
    if (toggleBtn.disabled) return;
    if (state === 'riding') {
      state = 'stopping'; setUI();
      // 目标倍率 = 剩余曲柄时间 / 期望滑行时长：恰好停在下车点（相位 0°）
      const delta = CRANK_PERIOD_MS - (crank.currentTime % CRANK_PERIOD_MS);
      applyRate(Math.min(1.35, Math.max(0.2, delta / 1400)));
    } else if (state === 'parked') {
      state = 'mounting'; setUI();
      svg.classList.add('mounting');
      collect();                               // 类切换重建了动画列表
      mountTimer = setTimeout(finishMount, 3100); // 兜底：上车序列总时长 2.75s
    }
  }

  // 上车序列播完（近腿踩到踏板 = 关键帧 0% 姿态）→ 摘类，骑行循环从规范相位 0 继续。
  // animationend 为主，超时兜底：页面被切后台时动画时钟冻结，事件可能迟到。
  let mountTimer = null;
  function finishMount() {
    if (state !== 'mounting') return;
    clearTimeout(mountTimer); mountTimer = null;
    svg.classList.remove('parked', 'mounting');
    collect();
    applyRate(+rateInput.value);
    state = 'riding'; setUI();
  }
  document.addEventListener('animationend', e => {
    if (state === 'mounting' && e.animationName === 'dismountLegNear') finishMount();
  });

  rateInput.addEventListener('input', () => applyRate(+rateInput.value));
  toggleBtn.addEventListener('click', toggle);
  window.addEventListener('keydown', e => {
    if (e.code === 'Space' && !(e.target instanceof HTMLInputElement)) {
      e.preventDefault(); toggle();
    }
  });

  // 读数循环：全部由 currentTime 推导（停车/驻车时读数自然归零冻结）
  let prevT = null, prevNow = null, emaRate = +rateInput.value;

  const fmtDist = m => m < 1000 ? m.toFixed(1) : (m / 1000).toFixed(2) + 'k';

  function frame(now) {
    if (rearWheel && crank) {
      const t = rearWheel.currentTime || 0;   // ms（含 playbackRate 与暂停的影响）
      const tc = crank.currentTime || 0;

      if (prevT !== null && now > prevNow) {
        const inst = (t - prevT) / (now - prevNow);
        emaRate += (inst - emaRate) * 0.25;   // 平滑实测倍率（含停止→0）
      }
      prevT = t; prevNow = now;

      kmhEl.textContent = (emaRate * BASE_KMH).toFixed(2);
      wrpmEl.textContent = (emaRate * 60000 / WHEEL_PERIOD_MS).toFixed(1);
      cadEl.textContent = (emaRate * 60000 / CRANK_PERIOD_MS).toFixed(1);
      distEl.textContent = fmtDist((t / WHEEL_PERIOD_MS) * WHEEL_CIRC_M);

      const thW = (t / WHEEL_PERIOD_MS * 360) % 360;        // 后轮相位角
      const thC2 = (tc / CRANK_PERIOD_MS * 720) % 360;      // 2×曲柄相位角
      let d = Math.abs(thW - thC2) % 360; if (d > 180) d = 360 - d;
      phaseEl.textContent = `θ轮 ${thW.toFixed(1)}° = 2·θ曲 ${thC2.toFixed(1)}° · Δ ${d.toFixed(2)}°`;
      lockBadge.textContent = d < 0.005 ? '已锁定' : `漂移 ${d.toFixed(2)}°`;

      // 停车滑行：只读currentTime检测曲柄过 0°（倍率≤1.35 → 每帧 <2°，无漏检）
      if (state === 'stopping') {
        const ang = (tc % CRANK_PERIOD_MS) / CRANK_PERIOD_MS * 360;
        if (prevAng !== null && prevAng > 300 && ang < 60) {
          state = 'parked';
          svg.classList.add('parked');       // CSS 接管：下车 + 放支架 + 驻车呼吸
          collect();
          applyRate(1);                      // 驻车小动画恢复正常速率
          rateInput.value = 1;               // 重新起步默认 ×1 巡航
          setUI();
        }
        prevAng = ang;
      } else {
        prevAng = null;
      }
    }
    requestAnimationFrame(frame);
  }

  window.addEventListener('load', () => {
    collect();
    applyRate(+rateInput.value);
    setUI();
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) {
      state = 'parked';                      // 减少动态偏好：直接呈现驻车画面
      svg.classList.add('parked');
      collect();
      applyRate(1);
      setUI();
    }
    requestAnimationFrame(frame);
  });
})();
