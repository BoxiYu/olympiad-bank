/* 奥数训练台 —— 页面交互（纯展示层：所有数值由服务端下发，此处不含任何契约常量） */

/* 标记 JS 可用：只有 JS 能用时才显示的控件（打印、主题切换）靠这个类现身，
   否则它们在禁用 JS 的浏览器里是点了没反应的死按钮。 */
document.documentElement.classList.add('has-js');

/* KaTeX 数学渲染（本地静态文件，不依赖外网） */
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
    try { localStorage.setItem('ob-theme', now); } catch (e) {}
  });
}

const fmtMMSS = (s) => {
  const sign = s < 0 ? '-' : '';
  s = Math.abs(s);
  return `${sign}${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};

/* 计时：服务端给已用秒数与限时，客户端只往前走表。
   显示语义与服务端渲染的初始值保持一致（有限时→剩余，无限时→已用），
   免得无 JS 的人看到「已用」、有 JS 的人看到「剩余」而同一个位置两种含义。 */
const timer = document.getElementById('timer');
if (timer) {
  const t0 = Date.now();
  const elapsed0 = Number(timer.dataset.elapsed) || 0;
  const limitS = (Number(timer.dataset.limit) || 0) * 60;
  const num = document.getElementById('timerNum');
  const cap = document.getElementById('timerCap');
  const capBase = cap.textContent.trim();
  const tick = () => {
    const elapsed = elapsed0 + Math.floor((Date.now() - t0) / 1000);
    if (limitS > 0) {
      const left = limitS - elapsed;
      num.textContent = fmtMMSS(left);
      num.classList.toggle('over', left < 0);
      cap.textContent = left >= 0 ? capBase : '已超时 · 不会打断你，但登记结果时会算进去';
    } else {
      num.textContent = fmtMMSS(elapsed);
      cap.textContent = capBase;
    }
  };
  tick();
  setInterval(tick, 1000);
}

/* 打印题目：只打题面，交给 @media print 处理版式 */
const printBtn = document.getElementById('printBtn');
if (printBtn) printBtn.addEventListener('click', () => window.print());

/* 就地二次确认：把提醒做进页面本身，不用原生 confirm()。
   原生弹窗会被隐私模式、部分扩展、内嵌浏览器面板静默屏蔽——一旦屏蔽 confirm() 返回 false，
   表单永不提交，学生看到的是「按钮点了没反应」，比报错更糟。就地确认无此依赖。
   语义仍是「只提醒不阻止」：第一次点亮出后果，再点一次即照常执行。 */
function armConfirm(form, btn, message, shouldAsk) {
  if (!form || !btn) return;
  let armed = false;
  const original = btn.innerHTML;
  const disarm = () => {
    armed = false;
    btn.innerHTML = original;
    btn.classList.remove('armed');
    if (btn.dataset.note) {
      const n = document.getElementById(btn.dataset.note);
      if (n) n.textContent = n.dataset.idle || '';
    }
  };
  form.addEventListener('submit', (e) => {
    if (typeof shouldAsk === 'function' && !shouldAsk()) return;   // 无需确认，直接放行
    if (armed) return;                                             // 已确认，照常提交
    e.preventDefault();
    armed = true;
    btn.innerHTML = '⚠️ 再点一次确认';
    btn.classList.add('armed');
    const n = btn.dataset.note ? document.getElementById(btn.dataset.note) : null;
    if (n) {
      n.dataset.idle = n.dataset.idle ?? n.textContent;
      n.textContent = message;
    }
    setTimeout(() => { if (armed) disarm(); }, 8000);              // 8 秒未确认自动复位
  });
}

/* 通用：任何带 data-confirm 的按钮都走就地二次确认（看答案 / 不做了 / 换成这道） */
document.querySelectorAll('[data-confirm]').forEach((btn) => {
  armConfirm(btn.closest('form'), btn, btn.dataset.confirm, () => true);
});

/* 提示解锁：冷却期内才需要确认（不拦截，与约定一致——照常给，只记一笔「提前看的」） */
const hintForm = document.getElementById('hintForm');
if (hintForm) {
  const cd0 = Number(hintForm.dataset.cooldown) || 0;
  const cdMin = hintForm.dataset.cooldownMin;
  // 第 1 条提示的参照物是「开始做这道题」，之后是「上一条提示」——服务端下发，别在这里猜
  const from = hintForm.dataset.cooldownFrom || '上一条提示';
  const note = document.getElementById('hintNote');
  const t0 = Date.now();
  const left = () => cd0 - Math.floor((Date.now() - t0) / 1000);
  const tick = () => {
    const l = left();
    note.textContent = l > 0
      ? `建议再自己想 ${Math.ceil(l / 60)} 分钟再看（约定是从${from}起隔 ${cdMin} 分钟）`
      : '';
  };
  tick();
  if (cd0 > 0) setInterval(tick, 1000);
  const hintBtn = document.getElementById('hintBtn');
  if (hintBtn) hintBtn.dataset.note = 'hintNote';
  armConfirm(hintForm, hintBtn,
    `现在看不会被阻止，只是会在日志里记一笔「提前看的」（约定是从${from}起隔 ${cdMin} 分钟）`,
    () => left() > 0);
}

/* 刚解锁的提示 / 刚亮出的答案：滚动到位并高亮，别让学生以为点了没反应 */
(() => {
  const id = location.hash.slice(1);
  if (!id) return;
  const el = document.getElementById(id);
  if (!el) return;
  el.scrollIntoView({ block: 'center' });
  el.classList.add('just-unlocked');
  el.focus({ preventScroll: true });
  setTimeout(() => el.classList.remove('just-unlocked'), 1600);
})();

/* 收卷表单：已看答案时，「合上答案重写一遍」的回答即时预选判定
   （服务端 finish_suggestion 才是正本；这里只是让学生少点一次） */
const finishForm = document.getElementById('finishForm');
if (finishForm && finishForm.dataset.revealed === '1') {
  finishForm.querySelectorAll('input[name="retell"]').forEach((r) => {
    r.addEventListener('change', () => {
      const target = r.value === 'yes' ? 'solution_reconstructed' : 'fail';
      const el = finishForm.querySelector(`input[name="result"][value="${target}"]`);
      if (el) {
        el.checked = true;
        el.closest('.opt').classList.add('just-unlocked');
        setTimeout(() => el.closest('.opt').classList.remove('just-unlocked'), 1600);
      }
    });
  });
}
/* 没做出来时软性要求选卡点：未选则就地确认一次 */
if (finishForm) {
  const submitBtn = finishForm.querySelector('button[type="submit"]');
  armConfirm(finishForm, submitBtn,
    '这次没自己做出来。选一下你最早卡在哪一步，下次才知道该补什么',
    () => {
      const result = finishForm.querySelector('input[name="result"]:checked');
      const stuck = finishForm.querySelector('input[name="stuck"]:checked');
      return !!result && ['fail', 'solution_reconstructed'].includes(result.value)
             && !!stuck && !stuck.value;
    });
}
