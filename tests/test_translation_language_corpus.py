"""冻结语料护栏：真实 (原文, 译文) 配对上的误伤/漏检棘轮。

背景：CXB-520/522 七轮返工的共同根因是「单元测试全绿、真实语料净负」——
判据本身被测试钉住了，但没有任何测试衡量它们在真实语料上的整体误伤面。
本文件用 400 组从真实语料冻结的配对（tests/fixtures/translation_language_corpus/）
锁住闸门的整体行为：任何让误伤(fp)或漏检(fn)变差的改动在 PR 阶段就变红。

标注轴是**目标语言覆盖**（半翻/照抄/乱码），不是全维度翻译质量。
标注来源见 fixture 的 label_source：human-review-r1..r7 为七轮评审人工判定，
gate-clean-random-sample 与 independent-latin-ratio 为独立判据 + 人工抽看。

天花板 = 2026-08-07 当前检测器实测值（棘轮只紧不松）：
  en: fp 0 / fn 4    —— CXB-520 修复 7 条短英文误伤后同步收紧
  zh: fp 0 / fn 139  —— master 尚无中文语言闸门；CXB-522 中文侧落地时应收紧到个位数
改善后请把常量往下调并在提交信息里写明依据；调松必须给出与本文件同等级的实测证据。
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'scripts'))
from translation_fidelity import verify_translation  # noqa: E402

FIXDIR = pathlib.Path(__file__).parent / 'fixtures' / 'translation_language_corpus'

CEILINGS = {
    'en': {'fp': 0, 'fn': 4},
    'zh': {'fp': 0, 'fn': 139},
}


def _run(lang):
    cases = json.loads((FIXDIR / f'{lang}.json').read_text(encoding='utf-8'))['cases']
    fp_ids, fn_ids = [], []
    for c in cases:
        findings = verify_translation(
            c['source'], c['translated'], mode='translated',
            target_lang=lang, source_lang=c.get('source_lang'),
        )
        if c['label'] == 'acceptable' and findings:
            fp_ids.append(c['mathnet_id'])
        if c['label'] == 'degraded' and not findings:
            fn_ids.append(c['mathnet_id'])
    return fp_ids, fn_ids


def test_en_language_corpus_ceilings():
    fp, fn = _run('en')
    assert len(fp) <= CEILINGS['en']['fp'], f'en 误伤超天花板: {fp}'
    assert len(fn) <= CEILINGS['en']['fn'], f'en 漏检超天花板: {fn}'


def test_zh_language_corpus_ceilings():
    fp, fn = _run('zh')
    assert len(fp) <= CEILINGS['zh']['fp'], f'zh 误伤超天花板: {fp}'
    assert len(fn) <= CEILINGS['zh']['fn'], f'zh 漏检超天花板: {fn}'


def test_fixture_pairs_are_real_corpus_text():
    """fixture 完整性：每条都有非空原文/译文、合法标注与来源说明。"""
    for lang in ('en', 'zh'):
        data = json.loads((FIXDIR / f'{lang}.json').read_text(encoding='utf-8'))
        assert '逐字照录' in data['_provenance']
        assert len(data['cases']) == 200
        for c in data['cases']:
            assert c['label'] in ('acceptable', 'degraded')
            assert c['label_source']
            assert c['source'].strip() and c['translated'].strip()


def test_en_fixture_keeps_the_seven_short_english_regressions():
    data = json.loads((FIXDIR / 'en.json').read_text(encoding='utf-8'))
    labels = {case['mathnet_id']: case['label'] for case in data['cases']}
    ids = {'004a', '02ov', '0956', '0afy', '0bpp', '0fig', '0foj'}
    assert {mathnet_id: labels.get(mathnet_id) for mathnet_id in ids} == {
        mathnet_id: 'acceptable' for mathnet_id in ids
    }
