# docs/sources —— 官方赛题 PDF 弹药库

> 归档日期：2026-08-03
> 原用途：官方赛题 PDF 弹药库使用说明（PDF 落盘 + 浏览器文本抽取双通道）。
> 死因/取代者：题库供给全换 MathNet（决策①），官方 PDF 不再是入库弹药；目录随迁至 docs/archive/sources/。
> 勘误注：`scripts/browser_pdf_extract.js` 已随迁至 `scripts/archive/browser_pdf_extract.js`；文中「本目录」均指 docs/archive/sources/。

把官方赛题 PDF 放到本目录（如 `IMO2024SL.pdf`），会话内即可全文精读入库/终审。

## 文本通道（主力，2026-08-03 打通）

**不依赖 PDF 落盘**：经 Claude in Chrome 扩展在浏览器内抽取官方 PDF 全文
（同源 fetch + DecompressionStream 解压 + Tj/TJ 算子抽取，零外部依赖、CSP 免疫），
管线与踩坑记录见 `scripts/archive/browser_pdf_extract.js`。ISL 2024 全卷 20.6 万字符
（含全部解答区）已按此法抽取，官方答案清单在 `isl2024_answers_harvest.md`。
后续年份逐年重跑同一管线即可。

## 字节归档（可选）

双击本目录下的 **`download_isl_pdfs.command`** 可从官网批量下载 2015–2025 各年
Shortlist PDF 到本目录（约 1–3 MB/份，已存在自动跳过）。
注意：Chrome 曾对 imo-official.org 触发「多文件自动下载」站点级拦截（2026-08-03），
脚本走 curl 不受影响；已手动落盘 IMO2015SL.pdf 在 ~/Downloads，可自行移入。

当前已落盘：`IMO2015SL.pdf`、`IMO2024SL.pdf`（Shortlist 全卷），
`IMO2025-problems-eng.pdf`（IMO 2025 正赛六题官方英文题面；**六题均未入库**，待批）。

> 通道封锁背景：云端出网代理仅放行 GitHub/PyPI 等少数域名，imo-official.org
> 直连被拒（443/80 均 CONNECT 403）；WebFetch 可达但长 PDF 约 30–40 页截断；
> 全网镜像（GitHub/grep.app/Scribd/Studocu/AoPS printable）无完整解答文本；
> 桌面安全分层锁死终端与浏览器直驾——2026-08-02/03 实测记录。

## ISL 2024 终审账本 —— **五项全部关闭（2026-08-03）**

| 题 | 库内条目 | 终审结论 |
| --- | --- | --- |
| C3 | C-033 | ✅ 官方 Answer「n(n−1)/2」逐字一致；三引理证明、极端排列同构（对径对坐＝完全交错） |
| C5 | C-035 | ✅ 官方 Answer 与闭式逐字等价；J(S;T) 奇偶拆分与 trie 模型同构；verification 升 sourced |
| N1 | N-034 | ✅ 官方 Answer「{1,2,4,12}」一致；2^k·m 分解证明与库内思路一致 |
| N2 | N-035 | ✅ 官方 Answer「{t},{t,3t}」一致；WLOG 缩放+奇偶论证与库内一致 |
| G2 | G-033 | ✅ SL 原文措辞逐字核对（interior of side BC 版），出入项关闭 |

ISL 2024 相关条目 31 题（含正赛 P1–P6 全六题）全部 `verification: sourced`；
有官方 Answer 行的题及 G1–G8 全部入库，终审清单见 `isl2024_answers_harvest.md`。
