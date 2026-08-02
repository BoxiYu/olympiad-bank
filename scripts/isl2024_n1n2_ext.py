#!/usr/bin/env python3
"""ISL 2024 N1/N2（N-034/N-035）扩验（2026-08-02）。

N1：n 的每个正因数 d 满足 d+1|n 或 d+1 为素数 —— 穷举至 2×10^6，仅 {1,2,4,12}。
N2：有限集 S，∀a,b∈S ∃c∈S: a|b+2c —— 本原集（gcd=1）穷举元素 ≤64、大小 ≤4，
    仅 {1} 与 {1,3}（一般解为其倍集 {a}、{a,3a}）。

运行约需 2–3 分钟。
"""
from itertools import combinations
from math import gcd

# ---------------- N1 ----------------
L = 2 * 10**6
spf = list(range(L + 2))
i = 2
while i * i <= L + 1:
    if spf[i] == i:
        for j in range(i * i, L + 2, i):
            if spf[j] == j:
                spf[j] = i
    i += 1

sols = []
for n in range(1, L + 1):
    x = n
    f = {}
    while x > 1:
        p = spf[x]
        e = 0
        while x % p == 0:
            x //= p
            e += 1
        f[p] = e
    divs = [1]
    for p, e in f.items():
        divs = [d * p**k for d in divs for k in range(e + 1)]
    ok = True
    for d in divs:
        t = d + 1
        if n % t == 0:
            continue
        if t > 1 and spf[t] == t:
            continue
        ok = False
        break
    if ok:
        sols.append(n)
print(f"N1: 1..{L} 内的全部解 = {sols}")
assert sols == [1, 2, 4, 12]

# ---------------- N2 ----------------
M, K = 64, 4
good = []
for size in range(1, K + 1):
    for S in combinations(range(1, M + 1), size):
        g = 0
        for x in S:
            g = gcd(g, x)
        if g != 1:
            continue
        if all(any((b + 2 * c) % a == 0 for c in S) for a in S for b in S):
            good.append(S)
print(f"N2: 元素≤{M}、大小≤{K} 的全部本原解 = {good}")
assert good == [(1,), (1, 3)]
print("扩验通过")
