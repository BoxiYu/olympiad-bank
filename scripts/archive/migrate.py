#!/usr/bin/env python3
"""一次性迁移脚本：把 src/ 下的板块大文件拆成 problems/ 下一题一文件。"""
import re, os, sys, yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CATEGORIES = {
    'A': ('algebra', '代数', 'src/代数板块.md'),
    'N': ('number-theory', '数论', 'src/数论板块.md'),
    'C': ('combinatorics', '组合', 'src/组合板块.md'),
    'G': ('geometry', '几何', 'src/几何板块.md'),
}
INDEPENDENT = {'A-016', 'A-033', 'C-009', 'C-011', 'G-004', 'G-011'}
FIELD_NAMES = ['出处', '难度', '知识点', '题面', '答案', '解法要点', '来源']

CONTEST_RULES = [
    (r'IMO\s*(\d{4})?\s*Shortlist|IMO Shortlist', 'ISL'),
    (r'Shortlist', 'ISL'),
    (r'IMO', 'IMO'),
    (r'USAMO', 'USAMO'),
    (r'USAJMO', 'USAJMO'),
    (r'AIME', 'AIME'),
    (r'AMC 8', 'AMC 8'),
    (r'AMC 10', 'AMC 10'),
    (r'AMC 12', 'AMC 12'),
    (r'CMO|中国数学奥林匹克', 'CMO'),
    (r'全国高中数学联赛', '高联'),
    (r'华罗庚金杯|华杯赛', '华杯赛'),
]
SYSTEM_BY_CONTEST = {
    'IMO': 'IMO/ISL', 'ISL': 'IMO/ISL',
    'USAMO': 'AMC体系', 'USAJMO': 'AMC体系', 'AIME': 'AMC体系',
    'AMC 8': 'AMC体系', 'AMC 10': 'AMC体系', 'AMC 12': 'AMC体系',
    'CMO': '高联/CMO', '高联': '高联/CMO',
    '华杯赛': '小学/初中',
}
# AMC 8 属小初难度带，体系上归入 AMC 但备赛定位为小初
SYSTEM_OVERRIDE = {'AMC 8': '小学/初中'}


def classify(source_ref):
    contest = None
    for pat, name in CONTEST_RULES:
        if re.search(pat, source_ref):
            contest = name
            break
    m = re.search(r'(19[5-9]\d|20[0-4]\d)', source_ref)
    year = int(m.group(1)) if m else None
    system = SYSTEM_OVERRIDE.get(contest) or SYSTEM_BY_CONTEST.get(contest)
    return contest, year, system


def parse_entries(text):
    body = text.split('## 二、题目条目', 1)[1]
    entries = []
    for block in re.split(r'\n(?=### )', body):
        m = re.match(r'### ([A-Z]-\d{3})｜(.+)', block.strip())
        if not m:
            continue
        pid, title = m.group(1), m.group(2).strip()
        fields, current = {}, None
        for line in block.splitlines()[1:]:
            fm = re.match(r'- \*\*(出处|难度|知识点|题面|答案|解法要点|来源)\*\*：(.*)', line)
            if fm:
                current = fm.group(1)
                fields[current] = fm.group(2)
            elif current and line.strip():
                fields[current] += '\n' + re.sub(r'^  ', '', line)
        entries.append((pid, title, fields))
    return entries


def main():
    count = 0
    for prefix, (slug, zh, srcfile) in CATEGORIES.items():
        text = open(os.path.join(ROOT, srcfile), encoding='utf-8').read()
        # 知识点树 -> taxonomy/
        tree = text.split('## 一、知识点树', 1)[1].split('## 二、题目条目', 1)[0].strip().rstrip('-').strip()
        with open(os.path.join(ROOT, 'taxonomy', f'{slug}.md'), 'w', encoding='utf-8') as f:
            f.write(f'# {zh}板块 · 知识点树\n\n{tree}\n')
        outdir = os.path.join(ROOT, 'problems', slug)
        for pid, title, fields in parse_entries(text):
            assert pid[0] == prefix, pid
            missing = [k for k in FIELD_NAMES if k not in fields]
            assert not missing, f'{pid} missing {missing}'
            source_ref = fields['出处'].strip()
            contest, year, system = classify(source_ref)
            diff = int(re.search(r'★(\d)', fields['难度']).group(1))
            topics = [t.strip() for t in re.split(r'\s*/\s*', fields['知识点'].strip()) if t.strip()]
            fm = {
                'id': pid,
                'title': title,
                'category': slug,
                'contest': contest,
                'year': year,
                'system': system,
                'source_ref': source_ref,
                'difficulty': diff,
                'topics': topics,
                'verification': 'independent-derivation' if pid in INDEPENDENT else 'sourced',
                'source_url': fields['来源'].strip(),
            }
            if pid in INDEPENDENT:
                fm['verification_note'] = '答案为独立推导，建议二次复核'
            if contest == '华杯赛':
                fm['compliance'] = '华杯赛大陆赛事 2018 年停办（现香港赛区）；非教育部白名单赛事，仅作训练素材'
            yml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, width=10**6)
            doc = (f'---\n{yml}---\n\n# {pid}｜{title}\n\n'
                   f'## 题面\n\n{fields["题面"].strip()}\n\n'
                   f'## 答案\n\n{fields["答案"].strip()}\n\n'
                   f'## 解法要点\n\n{fields["解法要点"].strip()}\n')
            with open(os.path.join(outdir, f'{pid}.md'), 'w', encoding='utf-8') as f:
                f.write(doc)
            count += 1
    print(f'migrated {count} problems')


if __name__ == '__main__':
    main()
