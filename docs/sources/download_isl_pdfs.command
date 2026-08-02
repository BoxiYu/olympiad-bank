#!/bin/bash
# 一键下载 IMO Shortlist 官方 PDF（2015–2025）到本目录
# 来源：https://www.imo-official.org/problems/IMOyyyySL.pdf（官方站）
cd "$(dirname "$0")" || exit 1
echo "下载目录：$(pwd)"
ok=0; skip=0; fail=0
for y in 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025; do
  f="IMO${y}SL.pdf"
  if [ -s "$f" ]; then
    echo "跳过 $f（已存在）"; skip=$((skip+1)); continue
  fi
  echo -n "下载 $f ... "
  if curl -fSL --connect-timeout 20 -o "$f" "https://www.imo-official.org/problems/$f" 2>/dev/null \
     || curl -fSL --connect-timeout 20 -o "$f" "https://www.imo-official.org/assets/documents/problems/$y/$f" 2>/dev/null; then
    echo "OK（$(du -h "$f" | cut -f1)）"; ok=$((ok+1))
  else
    echo "失败"; rm -f "$f"; fail=$((fail+1))
  fi
done
echo
echo "完成：成功 $ok，跳过 $skip，失败 $fail"
echo "（此窗口可直接关闭）"
