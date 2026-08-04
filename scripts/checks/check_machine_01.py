#!/usr/bin/env python3
"""machine-01 批次：13 道数值/闭式答案题的独立机器核验。

契约见 run_checks.py 头注：CHECKS = {题号: callable}，callable() → (ok, method)。
每个核验都独立重算（穷举 / Burnside / sympy 精确求解 / 递推双实现），
不从题面答案反推；与 `## 答案` 小节的比对目标写死在断言里，
比对目标本身照录题文件（改题必须同步改这里，run_checks 会因 fail 拦下不同步）。

运行（单跑本批）：uv run --with sympy python scripts/checks/run_checks.py
"""
import itertools
import math
import random
from fractions import Fraction


def check_a002():
    """A-002b：x1+x2+x3=3 且 Σx³=Σx⁴ ⟹ x1=x2=x3=1。
    核验链：sympy 验证 x⁴-x³-x+1 = (x-1)²(x²+x+1) 且 x²+x+1 恒正；
    于是 Σ(xᵢ⁴-xᵢ³-xᵢ+1) = Σx⁴-Σx³-3+3 = 0 是非负项之和为零 ⟹ 每个 xᵢ=1。"""
    import sympy as sp
    x = sp.symbols('x')
    factored_ok = sp.expand((x - 1)**2 * (x**2 + x + 1) - (x**4 - x**3 - x + 1)) == 0
    positive_ok = sp.discriminant(x**2 + x + 1, x) < 0 and sp.Poly(x**2 + x + 1, x).LC() > 0
    witness_ok = (1 + 1 + 1 == 3) and (3 * 1**3 == 3 * 1**4)
    ok = factored_ok and positive_ok and witness_ok
    return ok, 'sympy 验证 (x-1)²(x²+x+1) 因式分解与判别式<0，等式链强制每项为零 ⟹ 唯一解 (1,1,1)'


def check_a003():
    """A-003：sympy 精确解方程组，过滤 |xy|≤25/9，比对 {(-1/3,-1/3),(5/3,5/3)}。"""
    import sympy as sp
    x, y = sp.symbols('x y', real=True)
    eq = x**2 + y**2 + x + y - x * y * (x + y) + sp.Rational(10, 27)
    sols = sp.solve([eq, sp.Eq(x, y)], [x, y])  # 先解对称线上的（答案声称的解都在 x=y 上）
    on_line = {(sp.nsimplify(a), sp.nsimplify(b)) for a, b in sols
               if a.is_real and abs(a * b) <= sp.Rational(25, 9)}
    expected = {(sp.Rational(-1, 3), sp.Rational(-1, 3)), (sp.Rational(5, 3), sp.Rational(5, 3))}
    if on_line != expected:
        return False, f'x=y 线上的解集 {on_line} 与答案不符'
    # 非对称解排查：数值网格 + 牛顿迭代找 eq=0 ∩ |xy|≤25/9 的根，全部应收敛回对称线
    stray = []
    for x0 in [i / 4 for i in range(-12, 13)]:
        for y0 in [i / 4 for i in range(-12, 13)]:
            try:
                s = sp.nsolve(eq.subs(y, y0 + (x - x0) * 0), x, x0, verify=False)
            except Exception:
                continue
            xv, yv = float(s), y0
            if abs(eq.subs({x: xv, y: yv})) < 1e-9 and abs(xv * yv) <= 25 / 9 + 1e-9:
                if abs(xv - yv) > 1e-6 and not any(abs(xv - a) + abs(yv - b) < 1e-6
                                                   for a, b in [(-1 / 3, -1 / 3), (5 / 3, 5 / 3)]):
                    stray.append((xv, yv))
    if stray:
        return False, f'数值排查发现疑似对称线外的解 {stray[:3]}'
    return True, 'sympy 精确解 x=y 截线 == 答案两解；25×25 数值网格排查未见约束域内其他根'


def check_a005():
    """A-005：面积 = ∫₀^{1/√2} (arccos x − arcsin x) dx，sympy 符号积分对比 2−√2。"""
    import sympy as sp
    x = sp.symbols('x')
    area = sp.integrate(sp.acos(x) - sp.asin(x), (x, 0, 1 / sp.sqrt(2)))
    ok = sp.simplify(area - (2 - sp.sqrt(2))) == 0
    return ok, f'sympy 符号积分 ∫₀^(1/√2)(arccos−arcsin) = {sp.simplify(area)}，与 2−√2 恒等'


def check_a012():
    """A-012：√3/sin20° − 1/cos20° = 4，符号恒等 + 50 位数值双验。"""
    import sympy as sp
    d20 = sp.pi / 9
    expr = sp.sqrt(3) / sp.sin(d20) - 1 / sp.cos(d20)
    sym_ok = expr.equals(sp.Integer(4))
    num_ok = abs(sp.N(expr, 50) - 4) < sp.Float(10) ** -45
    return bool(sym_ok and num_ok), 'sympy .equals(4) 符号判定 + 50 位精度数值 |expr−4|<1e-45'


def check_a022():
    """A-022：最优常数 C=9/10。见证点 x=y=z=−1/3 处比值恰为 9/10（Fraction 精确），
    并在约束面 x+y+z=−1 上做 2 万次播种随机采样证伪扫描（避开分母近零点）。"""
    p3 = 3 * Fraction(-1, 3)**3 + 1
    p5 = 3 * Fraction(-1, 3)**5 + 1
    if p3 / p5 != Fraction(9, 10):
        return False, f'见证点比值 {p3 / p5} ≠ 9/10'
    rng = random.Random(20260804)
    worst = Fraction(0)
    for _ in range(20000):
        scale = 10 ** rng.uniform(-2, 2)
        xv = Fraction(rng.uniform(-scale, scale)).limit_denominator(10**6)
        yv = Fraction(rng.uniform(-scale, scale)).limit_denominator(10**6)
        zv = -1 - xv - yv
        num = abs(xv**3 + yv**3 + zv**3 + 1)
        den = abs(xv**5 + yv**5 + zv**5 + 1)
        if den > Fraction(1, 10**6):
            worst = max(worst, num / den)
    if worst > Fraction(9, 10):
        return False, f'采样发现比值 {float(worst):.6f} > 9/10 的反例'
    return True, '见证点 (-1/3)³ 比值精确 = 9/10；约束面 2 万次播种采样无超界（分母<1e-6 的奇点邻域除外）'


def check_a026():
    """A-026：λ(n) = n(n+1)²/4。凹增序列锥的极端射线是 aᵢ=min(i,k)：对 n=2..40 逐 k 取最小
    比值（Fraction 精确）应恰在 k=1（全 1 序列）取到公式值；再随机凹序列采样证伪。"""
    def ratio(a):
        s1 = sum(Fraction(i) * ai for i, ai in enumerate(a, 1))
        s2 = sum(Fraction(ai)**2 for ai in a)
        return s1 * s1 / s2
    for n in range(2, 41):
        formula = Fraction(n) * (n + 1)**2 / 4
        rays = {k: ratio([min(i, k) for i in range(1, n + 1)]) for k in range(1, n + 1)}
        if min(rays.values()) != formula or rays[1] != formula:
            return False, f'n={n}: 极端射线最小比值 {min(rays.values())} ≠ 公式 {formula}'
    rng = random.Random(20260804)
    for _ in range(2000):
        n = rng.randint(2, 10)
        d = sorted((Fraction(rng.randint(1, 100)) for _ in range(n)), reverse=True)
        a = list(itertools.accumulate(d))
        if ratio(a) < Fraction(n) * (n + 1)**2 / 4:
            return False, f'随机凹序列 n={n} 比值低于公式——λ(n) 声称过大'
    return True, '极端射线枚举 n≤40：最小比值恰为 n(n+1)²/4（全 1 序列取等）；2000 条随机凹序列无反例'


def check_a030():
    """A-030：3π 是 cos(nx)·sin(2009x/n²) 周期的正整数 n = {1, 7}。
    周期要求 3·2009/n² ∈ ℤ ⟹ n² | 6027 = 3·7²·41 ⟹ n ∈ {1,7}（数论侧）；
    数值侧对 n=1..100 高精度验证 f(x+3π)≡f(x) 恰在 {1,7} 成立（双实现互证）。"""
    import sympy as sp
    divisor_side = {n for n in range(1, 6028) if 6027 % (n * n) == 0}
    if divisor_side != {1, 7}:
        return False, f'n²|6027 的解集 {divisor_side} ≠ {{1,7}}'
    passing = set()
    xs = [sp.Float(v, 40) for v in (0.317, 1.234, 2.718, 4.669, 7.389, 11.32)]
    for n in range(1, 101):
        def f(x, n=n):
            return sp.cos(n * x) * sp.sin(sp.Rational(2009, n * n) * x)
        if all(abs(sp.N(f(x + 3 * sp.pi) - f(x), 40)) < sp.Float(10)**-30 for x in xs):
            passing.add(n)
    if passing != {1, 7}:
        return False, f'数值周期检验通过集 {passing} ≠ {{1,7}}'
    return True, '数论侧 n²|6027 与数值侧 n≤100 高精度周期检验双实现互证，均得 {1,7}'


def check_c005():
    """C-005：5 人各随机指 2 人，存在三人互指三角的概率。6⁵=7776 全枚举精确计数。"""
    pairs = list(itertools.combinations(range(4), 2))  # 每人从其余 4 人选 2，同构编码
    hit = 0
    people = range(5)
    others = {p: [q for q in people if q != p] for p in people}
    for choice in itertools.product(range(6), repeat=5):
        picked = {p: {others[p][i] for i in pairs[choice[p]]} for p in people}
        if any(all(set(tri) - {p} <= picked[p] for p in tri)
               for tri in itertools.combinations(people, 3)):
            hit += 1
    got = Fraction(hit, 6**5)
    return got == Fraction(5, 108), f'6⁵=7776 全枚举：{hit}/7776 = {got}，对照 5/108'


def check_c010():
    """C-010：立方体 6 面黑白染色 mod 旋转 = 10。从两个生成元闭包出 24 阶旋转群，
    Burnside 计数（与逐染色轨道划分双实现互证）。"""
    g1 = (0, 1, 5, 4, 2, 3)   # 绕上下轴 90°：面序 U D F B L R → F→R→B→L→F
    g2 = (4, 5, 2, 3, 1, 0)   # 绕前后轴 90°：U→R→D→L→U

    def comp(p, q):
        return tuple(p[q[i]] for i in range(6))
    group = {tuple(range(6))}
    frontier = [tuple(range(6))]
    while frontier:
        new = [comp(g, p) for p in frontier for g in (g1, g2)]
        fresh = [p for p in new if p not in group]
        group.update(fresh)
        frontier = fresh
    if len(group) != 24:
        return False, f'旋转群闭包大小 {len(group)} ≠ 24'
    fixed = sum(2 ** _n_cycles(p) for p in group)
    burnside = fixed // 24
    orbits = set()
    for c in itertools.product((0, 1), repeat=6):
        orbits.add(min(tuple(c[p[i]] for i in range(6)) for p in group))
    ok = burnside == 10 and len(orbits) == 10
    return ok, f'24 阶旋转群 Burnside={burnside} 与轨道全枚举={len(orbits)} 双实现互证，对照 10'


def _n_cycles(perm):
    seen, cnt = set(), 0
    for i in range(len(perm)):
        if i not in seen:
            cnt += 1
            j = i
            while j not in seen:
                seen.add(j)
                j = perm[j]
    return cnt


def check_c014():
    """C-014：100..999 的数字和分布 → 保证三张同和的最少抽卡数（抽屉原理精确计算）。"""
    from collections import Counter
    cnt = Counter(sum(map(int, str(n))) for n in range(100, 1000))
    worst_without_three = sum(min(2, c) for c in cnt.values())
    got = worst_without_three + 1
    return got == 53, f'27 个数字和的分布逐一统计：最坏 {worst_without_three} 张不出三同和，故答案 {got}，对照 53'


def check_c015():
    """C-015b：陪审团最多收 2ⁿ−n−1 欧元。n=2..5 全排列 × 记忆化最长路径穷举
    （移动使位置和严格减，状态图无环）。"""
    import functools

    def max_euros(n):
        @functools.lru_cache(maxsize=None)
        def best(order):
            top = 0
            for i in range(1, n + 1):
                pos = order.index(i)
                if pos >= i:
                    nxt = list(order)
                    nxt.pop(pos)
                    nxt.insert(pos - i, i)
                    top = max(top, 1 + best(tuple(nxt)))
            return top
        return max(best(p) for p in itertools.permutations(range(1, n + 1)))
    for n in range(2, 6):
        if max_euros(n) != 2**n - n - 1:
            return False, f'n={n} 穷举最大欧元数 {max_euros(n)} ≠ 2^{n}−{n}−1'
    return True, 'n=2..5 全排列初始态 × 记忆化最长路径穷举，最大收入均等于 2ⁿ−n−1'


def check_c038():
    """C-038：2020 人董事会博弈终态 1023 人。倒推递推双实现：续留者（继续流程仍在最终板）
    投驱逐、出局者投保留，保留需严格多数 ⟹ S(n) = n 若 n−S(n−1) > n/2 否则 S(n−1)。"""
    s_prev = 1
    sizes = {1: 1}
    for n in range(2, 2021):
        s_prev = n if n - s_prev > n / 2 else s_prev
        sizes[n] = s_prev
    stable = sorted({v for v in sizes.values()})
    mersenne_ok = all(v + 1 == 1 << (v + 1).bit_length() - 1 or (v + 1) & v == 0 for v in stable)
    got = sizes[2020]
    return got == 1023 and mersenne_ok, \
        f'策略投票递推 S(2020)={got}（稳定态均为 2^k−1：{stable[:6]}…），对照 1023'


def check_g005():
    """G-005：正十二面体的体对角线数。V=20、E=30、每个五边形面 5 条面对角线且不跨面共享：
    C(20,2) − 30 − 12×5 = 100（欧拉公式 V−E+F=2 交叉校验骨架数据）。"""
    v, e, f = 20, 30, 12
    if v - e + f != 2:
        return False, '欧拉公式校验失败'
    got = math.comb(v, 2) - e - f * 5
    return got == 100, f'C(20,2)−30−60 = {got}（V−E+F=2 校验通过），对照 100'


CHECKS = {
    'A-002': check_a002,
    'A-003': check_a003,
    'A-005': check_a005,
    'A-012': check_a012,
    'A-022': check_a022,
    'A-026': check_a026,
    'A-030': check_a030,
    'C-005': check_c005,
    'C-010': check_c010,
    'C-014': check_c014,
    'C-015': check_c015,
    'C-038': check_c038,
    'G-005': check_g005,
}
