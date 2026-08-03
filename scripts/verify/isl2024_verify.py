#!/usr/bin/env python3
"""ISL 2024 试点批次：对 A2/C3/C5/N1/N2 的答案做程序化独立验证。"""
from itertools import permutations
from functools import lru_cache
from collections import deque
import sys

# ---------- A2: min sum 2^i x_i^2, sum x_i = n  →  n(n+1)/2 ----------
def a2_min(n):
    best = {}
    def rec(i, remaining, acc):
        # 层数够用即可；剪枝：acc 已超当前最优则弃
        if remaining == 0:
            best[0] = min(best.get(0, 10**9), acc)
            return
        if i > n:
            return
        if acc >= best.get(0, 10**9):
            return
        for x in range(remaining, -1, -1):
            rec(i + 1, remaining - x, acc + (2**i) * x * x)
    rec(0, n, 0)
    return best[0]

a2_ok = all(a2_min(n) == n * (n + 1) // 2 for n in range(1, 11))
print(f"A2: min = n(n+1)/2 对 n=1..10 全部成立 -> {a2_ok}")

# ---------- C3: 2n 骑士圆桌，最坏初始排列下的最少相邻交换数 ----------
def c3_value(n):
    size = 2 * n
    full = (1 << n) - 1

    def shaken(arr):
        m = 0
        for i in range(size):
            if arr[i] == arr[(i + 1) % size]:
                m |= 1 << arr[i]
        return m

    def solve(start):
        s0 = (start, shaken(start))
        if s0[1] == full:
            return 0
        seen = {s0}
        dq = deque([(s0, 0)])
        while dq:
            (arr, m), d = dq.popleft()
            for i in range(size):
                j = (i + 1) % size
                na = list(arr)
                na[i], na[j] = na[j], na[i]
                na = tuple(na)
                nm = m | shaken(na)
                if nm == full:
                    return d + 1
                st = (na, nm)
                if st not in seen:
                    seen.add(st)
                    dq.append((st, d + 1))
        raise RuntimeError

    worst = 0
    seen_arr = set()
    base = []
    for p in range(n):
        base += [p, p]
    for perm in set(permutations(base)):
        # 圆桌旋转/翻转等价类只算一次（粗剪枝：固定 perm[0]==0）
        if perm[0] != 0:
            continue
        worst = max(worst, solve(perm))
    return worst

c3_vals = {n: c3_value(n) for n in (2, 3)}
print(f"C3: 最坏情形最少交换数 n=2,3 -> {c3_vals}（候选公式对照：n(n-1)/2 给 1,3；⌊n²/4⌋ 给 1,2）")

# ---------- C5: Geoff/Ceri 擦数游戏（擦掉最后一个数者输），先手必胜的 N ----------
def c5_first_wins(N):
    @lru_cache(maxsize=None)
    def win(mask):
        # 轮到行动方；若某步后 mask 变空，行动方擦了最后一个数 -> 行动方输
        nums = [i + 1 for i in range(N) if mask >> i & 1]
        for n in nums:
            k = 0
            seen_masks = set()
            while True:
                mod = 1 << k
                erase = 0
                for i in range(N):
                    if mask >> i & 1 and (n - (i + 1)) % mod == 0:
                        erase |= 1 << i
                nm = mask & ~erase
                if erase not in seen_masks:
                    seen_masks.add(erase)
                    if nm == 0:
                        pass          # 自己擦最后 -> 输，不选
                    elif not win(nm):
                        return True
                if erase == (1 << N) - 1 & mask or mod > 2 * N:
                    break
                k += 1
        return False
    return win((1 << N) - 1)

c5_pattern = {N: c5_first_wins(N) for N in range(1, 17)}
winners = [N for N, w in c5_pattern.items() if w]
print(f"C5: 先手必胜的 N（1..16）-> {winners}")

# ---------- N1: 所有 n 使得每个因数 d 满足 d+1|n 或 d+1 为素数 ----------
LIM = 10**6
def n1_check(limit):
    # 线性筛素数
    sieve = bytearray([1]) * 0
    is_comp = bytearray(limit + 2)
    primes = []
    for i in range(2, limit + 2):
        if not is_comp[i]:
            primes.append(i)
        for p in primes:
            if i * p > limit + 1:
                break
            is_comp[i * p] = 1
            if i % p == 0:
                break
    def is_prime(x):
        return x >= 2 and not is_comp[x]
    good = []
    for n in range(1, limit + 1):
        ok = True
        d = 1
        while d * d <= n:
            if n % d == 0:
                for dd in (d, n // d):
                    if not (is_prime(dd + 1) or n % (dd + 1) == 0):
                        ok = False
                        break
            if not ok:
                break
            d += 1
        if ok:
            good.append(n)
    return good

n1_good = n1_check(10**5)
print(f"N1: 1..1e5 内满足条件的 n -> {n1_good}")

# ---------- N2: 所有有限集 S：任意 a,b∈S 存在 c∈S 使 a | b+2c ----------
def n2_search(maxval=40, maxsize=4):
    from itertools import combinations
    survivors = []
    pool = range(1, maxval + 1)
    def ok(S):
        for a in S:
            for b in S:
                if not any((b + 2 * c) % a == 0 for c in S):
                    return False
        return True
    for size in range(1, maxsize + 1):
        for S in combinations(pool, size):
            from math import gcd
            g = 0
            for x in S:
                g = gcd(g, x)
            if g != 1:
                continue        # 只列本原集（可整体缩放）
            if ok(S):
                survivors.append(S)
    return survivors

n2_sets = n2_search()
print(f"N2: ≤40、大小≤4 的本原解集 -> {n2_sets}")
