#!/usr/bin/env python3
"""训练菜单：bank.py 的零参数入口（非程序员向）。

只做薄封装：每个菜单项就是一条 bank.py 命令，规则与流程正本在 docs/ 两手册与 SPEC.md，
本文件不持有任何训练规则。启动方式（三选一，效果相同）：
  macOS 双击 开始.command ｜ Windows 双击 开始.bat ｜ 终端 uv run python scripts/menu.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = os.path.join(ROOT, 'scripts', 'bank.py')

# 目标赛事清单的正本在 bank.py PLAN_PROFILES；此处仅为菜单提示顺序
TARGETS = ['AMC8', 'AMC10', 'AMC12', 'AIME', '高联一试', '高联加试', 'CMO', 'USAMO', 'IMO']

MENU = """
════════ 奥数训练 ════════
 1) 开卡训练（复习到期优先）
 2) 卡住了 → 解锁下一级提示
 3) 弹尽粮绝 → 看解法要点（之后须合卷复述）
 4) 收卡登记结果
 5) 到期复习一览
 6) 出本周训练计划（教练）
 7) 我的能力图
 8) 题库规模与分布
 0) 退出
══════════════════════════"""


def bank(*argv):
    """转发给 bank.py；交互（如 finish 的问答）直通终端。"""
    return subprocess.call([sys.executable, BANK, *argv], cwd=ROOT)


def pick_target():
    print('目标赛事：' + '  '.join(f'{i + 1}={t}' for i, t in enumerate(TARGETS)))
    c = input(f'选数字（回车默认 4={TARGETS[3]}）: ').strip()
    try:
        return TARGETS[int(c) - 1]
    except (ValueError, IndexError):
        return TARGETS[3]


def pick_student():
    sid = input('学生 id（回车默认 self）: ').strip() or 'self'
    return sid


def main():
    os.chdir(ROOT)
    print('欢迎！本菜单是命令行工具的简化入口；训练纪律见 docs/学生手册.md（一页纸）。')
    while True:
        print(MENU)
        c = input('选数字: ').strip()
        if c == '1':
            bank('spar', 'next')
        elif c == '2':
            bank('spar', 'hint')
        elif c == '3':
            bank('spar', 'reveal')
        elif c == '4':
            bank('spar', 'finish')
        elif c == '5':
            bank('review')
        elif c == '6':
            t = pick_target()
            bank('coach', '--target', t, '--save')
        elif c == '7':
            sid = pick_student()
            if bank('profile', sid, '--html') != 0:
                print('提示：教练先建档 → uv run python scripts/bank.py student add <id> --name 姓名')
        elif c == '8':
            bank('stats')
        elif c in ('0', 'q', ''):
            print('再见！')
            return 0
        else:
            print('没有这个选项，请输入菜单里的数字。')
        input('\n（回车返回菜单）')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)
