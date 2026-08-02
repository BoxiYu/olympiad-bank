#!/usr/bin/env python3
"""对 6 道 independent-derivation 题做符号/数值二次复核（2026-08-02）。"""
from fractions import Fraction
import itertools, math
import sympy as sp

results = []

def check(pid, desc, ok, detail):
    results.append((pid, desc, ok, detail))

# ---------- G-004: 3-4-5 直角三角形绕三边旋转，最大:最小体积 = 5:3 ----------
V3 = Fraction(1, 3) * 4**2 * 3          # /pi，绕边3：圆锥 r=4 h=3
V4 = Fraction(1, 3) * 3**2 * 4          # 绕边4
h = Fraction(12, 5)                      # 斜边上的高
V5 = Fraction(1, 3) * h**2 * 5           # 绕斜边：两共底圆锥，高之和=5
ratio = max(V3, V4, V5) / min(V3, V4, V5)
check('G-004', '最大:最小体积比', ratio == Fraction(5, 3),
      f'V=({V3},{V4},{V5})π, 比值={ratio}')

# ---------- C-009 / G-011: 12 条直线，夹角和 3240° ----------
# 方向 0..11（单位 15°），两两夹角 = 15*min(d,12-d)
total = sum(15 * min(j - i, 12 - (j - i))
            for i, j in itertools.combinations(range(12), 2))
# L_max=12 的下界构造成立（12 个方向两两不同→两两相交），上界：方向角差必为 15° 倍数
check('C-009/G-011', 'L=12 时夹角总和', total == 3240, f'sum={total}°')

# ---------- C-011: (xy-5x+3y-15)^n 项数 ≥ 2021 的最小 n ----------
x, y = sp.symbols('x y')
n_test = 44
expanded = sp.expand((x*y - 5*x + 3*y - 15)**6)  # 小规模验证 (n+1)^2 规律
cnt6 = len(expanded.as_ordered_terms())
formula_ok = cnt6 == 49  # (6+1)^2
n_min = next(n for n in range(1, 100) if (n + 1)**2 >= 2021)
check('C-011', '项数=(n+1)^2 且 n_min=44', formula_ok and n_min == 44,
      f'n=6 时实测 {cnt6} 项（应 49）；(44+1)^2={45**2}>=2021, (43+1)^2={44**2}<2021')

# ---------- A-016: a-b 的取值范围 (-inf, 25/9] ----------
s_ = sp.symbols('s', real=True)
expr = -9*s_**2 - 26*s_ - 16            # a-b 关于较小根 s 的表达式（r=9s+16）
# 复核推导链：roots r>s, 四根 -1±p, -1±q, p=sqrt(r+2), q=sqrt(s+2), 等差 <=> p=3q
r_ = 9*s_ + 16
a_ = -(r_ + s_)                          # a = -(r+s)
b_ = r_ * s_
assert sp.simplify((a_ - b_) - expr) == 0
vmax = sp.maximum(expr, s_, sp.Interval.open(-2, sp.oo))
# 等差数列验证：p=3q 时四根 -1-3q,-1-q,-1+q,-1+3q 公差 2q ✓
q_ = sp.symbols('q', positive=True)
roots = [-1 - 3*q_, -1 - q_, -1 + q_, -1 + 3*q_]
diffs = {sp.simplify(roots[i+1] - roots[i]) for i in range(3)}
check('A-016', 'max(a-b)=25/9 且可取到、下方无界', vmax == sp.Rational(25, 9) and diffs == {2*q_},
      f'vertex s=-13/9∈(-2,∞), max={vmax}; s→∞ 时 a-b→-∞')

# ---------- A-033 (1): a1 - b1 = 199 ----------
# 数值验证不变量：a_{n+1} = a1/(a1+1) * a_n；b_{n+1} = (b1+2n)/(b1+2n-1) * b_n
def simulate(a1, b1, N=120):
    a, b = [a1], [b1]
    Ta, Tb = 1 + 1/a1, 1 + 1/b1
    for n in range(1, N):
        a.append(a[-1] - 1/Ta); Ta += 1/a[-1]
        b.append(b[-1] + 1/Tb); Tb += 1/b[-1]
    return a, b

a1v = 400.0
b1v = a1v - 199
a, b = simulate(a1v, b1v)
ra = a1v / (a1v + 1)
inv_a = max(abs(a[n+1] - ra*a[n]) for n in range(110))
inv_b = max(abs(b[n+1] - (b1v + 2*(n+1)) / (b1v + 2*(n+1) - 1) * b[n]) for n in range(110))
cond = abs(a[99]*b[99] - a[100]*b[100])          # a_100 b_100 = a_101 b_101 (0-indexed)
# 反向：扰动 a1 使差不为 199，条件应破坏
a2, b2 = simulate(a1v + 5, b1v)
cond_bad = abs(a2[99]*b2[99] - a2[100]*b2[100])
check('A-033(1)', 'a1-b1=199 ⇔ a100b100=a101b101',
      inv_a < 1e-9 and inv_b < 1e-9 and cond < 1e-9 and cond_bad > 1e-6,
      f'不变量残差 {inv_a:.1e}/{inv_b:.1e}; 条件残差 {cond:.2e}（a1-b1=199）, {cond_bad:.2e}（=204）')

# ---------- A-033 (2): a100=b99 ⇒ a100+b100 > a101+b101 ----------
ok2 = True
detail2 = []
for b1v in (0.5, 3.0, 50.0):
    # a_n = a1 r^{n-1}, r=a1/(a1+1)；解 a1 使 a100 = b99
    _, bb = simulate(1.0, b1v)
    target = bb[98]                                   # b_99（0-indexed）
    f = lambda a1: a1 * (a1/(a1+1))**99 - target
    lo, hi = 1e-6, 1e6
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(mid) < 0: lo = mid
        else: hi = mid
    a1v = (lo + hi) / 2
    aa, bb = simulate(a1v, b1v)
    s100 = aa[99] + bb[99]; s101 = aa[100] + bb[100]
    ok2 &= s100 > s101
    detail2.append(f'b1={b1v}: a1={a1v:.4f}, s100-s101={s100-s101:.3e}')
check('A-033(2)', 'a100+b100 > a101+b101', ok2, '; '.join(detail2))

for pid, desc, ok, detail in results:
    print(f"{'PASS' if ok else 'FAIL'}  {pid:<14} {desc}\n      {detail}")
fails = [r for r in results if not r[2]]
print(f"\n{len(results)-len(fails)}/{len(results)} 项通过")
exit(1 if fails else 0)
