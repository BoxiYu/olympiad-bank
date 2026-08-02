# docs/sources —— 官方赛题 PDF 弹药库

把官方赛题 PDF 放到本目录（如 `IMO2024SL.pdf`），会话内即可全文精读入库/终审。

## 一键补齐历年 ISL PDF

双击本目录下的 **`download_isl_pdfs.command`**（macOS 会打开终端自动执行），
即从 imo-official.org 官网把 2015–2025 各年 Shortlist PDF 下载到本目录，每份约 1–3 MB。
脚本只用系统自带 curl，逐年报告成功/失败，已存在的文件自动跳过。

> 为什么需要这一步：云端会话的出网代理仅放行 GitHub/PyPI 等少数域名，
> imo-official.org 直连被拒（443/80 均 CONNECT 403）；WebFetch 可达但对长 PDF
> 约在 30–40 页处截断，解答区拿不到。全网镜像检索（GitHub API/grep.app/
> Scribd/Studocu/AoPS printable 合集）均无完整解答文本——2026-08-02 实测记录。

## ISL 2024 终审待办（PDF 落库后逐项关闭）

| 题 | 库内条目 | 现有证据 | 待终审项 |
| --- | --- | --- | --- |
| C3 | C-033 | BFS 穷举 n≤4 吻合 n(n-1)/2 | 一般 n 的官方证明比对 |
| C5 | C-035 | 双实现求解器 N≤48 互证 + 闭式盲验 8/8 | 官方答案/解法比对 |
| N1 | N-034 | 穷举 2×10⁶ 内仅 {1,2,4,12} | 「无其他解」官方证明比对 |
| N2 | N-035 | 本原集穷举（≤64，大小≤4）仅 {1},{1,3} | 完整分类官方证明比对 |
| G2 | G-033 | ~~措辞出入~~ **已终审关闭**（2026-08-02 SL 原文逐字核对） | — |

终审后：verification 由 independent-derivation 升为 sourced，并在 verification_note 记录比对结论。
