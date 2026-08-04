/* 奥数训练台 —— 页面交互（纯展示层：所有数值由服务端下发，此处不含任何契约常量） */

/* KaTeX 数学渲染（CDN 加载完成后） */
document.addEventListener('DOMContentLoaded', () => {
  if (window.renderMathInElement) {
    renderMathInElement(document.body, {
      delimiters: [
        { left: '$$', right: '$$', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false },
        { left: '\\[', right: '\\]', display: true },
      ],
      throwOnError: false,
    });
  }
});

/* 明暗切换 */
const themeBtn = document.getElementById('themeBtn');
if (themeBtn) {
  themeBtn.addEventListener('click', () => {
    const r = document.documentElement;
    const cur = r.getAttribute('data-theme');
    const sysDark = matchMedia('(prefers-color-scheme: dark)').matches;
    const now = cur ? (cur === 'dark' ? 'light' : 'dark') : (sysDark ? 'light' : 'dark');
    r.setAttribute('data-theme', now);
    localStorage.setItem('ob-theme', now);
  });
}

const fmtMMSS = (s) => {
  const sign = s < 0 ? '-' : '';
  s = Math.abs(s);
  return `${sign}${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/* 攻坚计时：服务端给已用秒数与限时，客户端只往前走表 */
const timer = document.getElementById('timer');
if (timer) {
  const t0 = Date.now();
  const elapsed0 = Number(timer.dataset.elapsed) || 0;
  const limitS = (Number(timer.dataset.limit) || 0) * 60;
  const num = document.getElementById('timerNum');
  const cap = document.getElementById('timerCap');
  const tick = () => {
    const elapsed = elapsed0 + Math.floor((Date.now() - t0) / 1000);
    if (limitS > 0) {
      const left = limitS - elapsed;
      num.textContent = fmtMMSS(left);
      num.classList.toggle('over', left < 0);
      cap.textContent = left >= 0 ? `剩余（限时 ${limitS / 60} 分钟）` : `已超时（限时 ${limitS / 60} 分钟，超时不拦，计入判定）`;
    } else {
      num.textContent = fmtMMSS(elapsed);
      cap.textContent = '已用时';
    }
  };
  tick();
  setInterval(tick, 1000);
}

/* 提示解锁：冷却期内确认（不拦截，与纪律一致——照常解锁但记 early） */
const hintForm = document.getElementById('hintForm');
if (hintForm) {
  const cd0 = Number(hintForm.dataset.cooldown) || 0;
  const cdMin = hintForm.dataset.cooldownMin;
  const note = document.getElementById('hintNote');
  const t0 = Date.now();
  const left = () => cd0 - Math.floor((Date.now() - t0) / 1000);
  const tick = () => {
    const l = left();
    note.textContent = l > 0 ? `建议再独立奋战 ${fmtMMSS(l)} 再解锁（纪律：每级之间 ${cdMin} 分钟）` : '';
  };
  tick();
  if (cd0 > 0) setInterval(tick, 1000);
  hintForm.addEventListener('submit', (e) => {
    if (left() > 0 && !confirm(`纪律是每级提示之间再独立奋战 ${cdMin} 分钟。\n现在解锁不会被阻止，但会打上「提前解锁」标记进日志。\n\n确定现在解锁吗？`)) {
      e.preventDefault();
    }
  });
}

/* 看解法：不可逆动作确认 */
const revealForm = document.getElementById('revealForm');
if (revealForm) {
  revealForm.addEventListener('submit', (e) => {
    if (!confirm('看解法会永久记入本次日志——之后判定只能是「看解后复述通过」或「未通过」。\n\n确定看吗？')) {
      e.preventDefault();
    }
  });
}

/* 放弃本卷确认 */
const abandonForm = document.getElementById('abandonForm');
if (abandonForm) {
  abandonForm.addEventListener('submit', (e) => {
    if (!confirm('放弃本卷不会写任何训练记录，这道题之后还会正常出现。\n\n确定放弃吗？')) {
      e.preventDefault();
    }
  });
}

/* 收卷表单：已看答案时，「复述通过？」按契约驱动判定默认值（通过→solution_reconstructed，否→fail） */
const finishForm = document.getElementById('finishForm');
if (finishForm && finishForm.dataset.revealed === '1') {
  finishForm.querySelectorAll('input[name="retell"]').forEach((r) => {
    r.addEventListener('change', () => {
      const target = r.value === 'yes' ? 'solution_reconstructed' : 'fail';
      const el = finishForm.querySelector(`input[name="result"][value="${target}"]`);
      if (el) el.checked = true;
    });
  });
}
/* fail / 复述通过 时软性要求卡点：未选则确认一次 */
if (finishForm) {
  finishForm.addEventListener('submit', (e) => {
    const result = finishForm.querySelector('input[name="result"]:checked');
    const stuck = finishForm.querySelector('input[name="stuck"]:checked');
    if (result && ['fail', 'solution_reconstructed'].includes(result.value) && stuck && !stuck.value) {
      if (!confirm('这次没独立做出来——卡点标签是教练诊断的金矿，建议选一个「最早卡住的环节」。\n\n不选直接落账吗？')) {
        e.preventDefault();
      }
    }
  });
}
