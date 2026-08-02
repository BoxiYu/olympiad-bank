#!/usr/bin/env python3
"""ISL 2024 C5（C-035）：Geoff–Ceri 擦数博弈的双实现互证求解器。

游戏：板上 1..N，轮流选 (k, n)（n 在板上），擦去所有 2^k | n-s 的 s；
擦去最后一个数者负。求 Geoff（先手）必胜的所有 N。

实现 A（naive）：frozenset 全态搜索，适用 N ≤ ~18。
实现 B（smart）：把数挂到二进制低位 trie 上，操作＝删某节点整棵子树；
  局面按「树形状」正则化（子树排序 + 单链收缩）后记忆化，适用 N ≤ ~48。

闭式（由 N≤40 数据归纳、N=41..48 盲验 8/8）：
  N = 2^a·m（m 奇）。Geoff 必胜 ⟺ (m≥3 且 a 偶) 或 (m=1 且 a 奇)。

运行：python3 isl2024_c5_game.py           # 默认互证到 N=18、smart 到 N=40
      python3 isl2024_c5_game.py 48        # smart 算到指定 N（48 约需数分钟）
"""
import sys
from functools import lru_cache

sys.setrecursionlimit(2_000_000)


# ---------------- 实现 A：朴素全态搜索 ----------------
def naive_win(N):
    K = 0
    while (1 << K) <= N:
        K += 1
    Ks = range(K + 1)
    memo = {}

    def win(S):
        r = memo.get(S)
        if r is not None:
            return r
        succ = set()
        for n in S:
            for k in Ks:
                m = 1 << k
                succ.add(S - frozenset(s for s in S if (n - s) % m == 0))
        res = any(S2 and not win(S2) for S2 in succ)
        memo[S] = res
        return res

    return win(frozenset(range(1, N + 1)))


# ---------------- 实现 B：trie 形状正则化 ----------------
def build(vals):
    if len(vals) == 1:
        return ('L',)
    odds = tuple(v >> 1 for v in vals if v & 1)
    evens = tuple(v >> 1 for v in vals if not v & 1)
    kids = [build(p) for p in (odds, evens) if p]
    if len(kids) == 1:
        return kids[0]  # 单链收缩：父/子删除效果相同
    return ('B', tuple(sorted(kids, key=repr)))


def mk(x, y):
    if x is None:
        return y
    if y is None:
        return x
    return ('B', tuple(sorted((x, y), key=repr)))


@lru_cache(maxsize=None)
def succs(T):
    out = {None}  # None＝整棵删掉
    if T != ('L',):
        a, b = T[1]
        for a2 in succs(a):
            out.add(mk(a2, b))
        for b2 in succs(b):
            out.add(mk(a, b2))
    return frozenset(out)


WIN = {}


def smart_win_tree(T):
    r = WIN.get(T)
    if r is None:
        r = WIN[T] = any(S is not None and not smart_win_tree(S) for S in succs(T))
    return r


def smart_win(N):
    return smart_win_tree(build(tuple(range(1, N + 1))))


# ---------------- 闭式 ----------------
def closed_form(N):
    a = 0
    while N % 2 == 0:
        N //= 2
        a += 1
    m = N
    return (m >= 3 and a % 2 == 0) or (m == 1 and a % 2 == 1)


if __name__ == '__main__':
    hi = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    for N in range(1, 19):
        assert naive_win(N) == smart_win(N), f"两实现在 N={N} 不一致！"
    print("互证 OK：naive 与 smart 在 N=1..18 完全一致")
    bad = [N for N in range(1, hi + 1) if smart_win(N) != closed_form(N)]
    print(f"闭式核对 N=1..{hi}：{'全部吻合' if not bad else f'不吻合 {bad}'}")
    print("Geoff 必败集：", [N for N in range(1, hi + 1) if not smart_win(N)])
