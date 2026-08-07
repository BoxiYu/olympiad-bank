"""译文保真校验器的自造配对样本；不依赖 mathnet-full/，不触碰 problems/。"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from translation_fidelity import (  # noqa: E402
    BatchConfig,
    FindingType,
    main,
    verify_batch,
    verify_directory,
    verify_translation,
)
import translation_language as language_rules  # noqa: E402
from translation_language import (  # noqa: E402
    LanguageConfig,
    has_translatable_prose,
    is_pure_symbol,
    plain_prose,
    target_language_mismatch,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'translation_fidelity'


SOURCE = r'''# sample-001

- mathnet_id: sample-001
- contest: Sample Olympiad

## 题面
Find integers $x \leq y$ such that `x + y` is even.

![](attached_image_1.png)

$$
x + y = 10
$$

\begin{align}
x-y &= 2
\end{align}

## 解法 1
Set $x=4$ and verify the equality.

## 解法 2
Alternatively use $y=6$.

## 最终答案
D
'''

VALID_TRANSLATION = r'''# sample-001

- mathnet_id: sample-001
- contest: Sample Olympiad（样例奥林匹克）

## 题面
求整数 $x \leq y$，使 `x + y` 为偶数。

![](attached_image_1.png)

$$
x + y = 10
$$

\begin{align}
x-y &= 2
\end{align}

## 解法 1
令 $x=4$，并验证等式。

## 解法 2
也可以使用 $y=6$。

## 最终答案
D
'''


def types(source=SOURCE, translated=VALID_TRANSLATION, **kwargs):
    return {finding.type for finding in verify_translation(source, translated, **kwargs)}


def test_legal_translation_has_zero_findings():
    """自然语言可翻译；数学、代码、图片、骨架和纯符号答案原样保留。"""
    assert verify_translation(SOURCE, VALID_TRANSLATION) == []


def human_reported_degenerate_fixture():
    payload = json.loads(
        (FIXTURES / 'human-reported-degenerate-excerpts.json').read_text(encoding='utf-8')
    )
    assert payload['fixture_kind'] == 'human-reported translated-unit excerpts'
    return payload


def full_document_feature_fixture():
    return json.loads(
        (FIXTURES / 'cxb-513-human-reported-full-document-features.json')
        .read_text(encoding='utf-8')
    )


def placeholder_reordering_fixture():
    return json.loads(
        (FIXTURES / 'cxb-525-placeholder-reordering.json').read_text(encoding='utf-8')
    )


def placeholder_reordering_documents():
    fixture = placeholder_reordering_fixture()
    source_unit = fixture['source_unit']
    translated_unit = fixture['translated_unit']
    for placeholder, original in fixture['protected'].items():
        source_unit = source_unit.replace(placeholder, original)
        translated_unit = translated_unit.replace(placeholder, original)
    source = f'# cxb525\n\n## 题面\n\n{source_unit}\n\n## 最终答案\n\nD\n'
    translated = f'# cxb525\n\n## 题面\n\n{translated_unit}\n\n## 最终答案\n\nD\n'
    return source, translated


def audit_fixture():
    return json.loads((FIXTURES / 'full-pair-audit.json').read_text(encoding='utf-8'))


def zh_language_fixture():
    return json.loads(
        (FIXTURES / 'zh-language-constructed.json').read_text(encoding='utf-8')
    )


def language_document(body, heading='题面'):
    return f'# constructed-language-case\n\n## {heading}\n\n{body}\n'


def test_zh_language_fixture_is_explicitly_constructed_without_real_ids():
    fixture = zh_language_fixture()
    assert fixture['fixture_kind'].startswith('explicitly constructed')
    assert all('mathnet_id' not in row for row in fixture['rows'])
    assert all(row['fixture_id'].startswith('constructed-') for row in fixture['rows'])


@pytest.mark.parametrize(
    'row', zh_language_fixture()['rows'], ids=lambda row: row['fixture_id'],
)
def test_constructed_zh_language_fixture_findings(row):
    findings = verify_translation(
        language_document(row['source_body']),
        language_document(row['translated_body']),
        target_lang='zh',
    )
    language_types = [
        finding.type.value for finding in findings
        if finding.type in {
            FindingType.TARGET_LANGUAGE_MISMATCH,
            FindingType.MOJIBAKE,
        }
    ]
    expected = [] if row['expected'] == 'clean' else [row['expected']]
    assert language_types == expected


def test_identical_zh_section_emits_untranslated_without_duplicate_mismatch():
    document = language_document(
        'Find all positive integers satisfying the condition.'
    )
    findings = verify_translation(document, document, target_lang='zh')
    assert [finding.type for finding in findings] == [FindingType.UNTRANSLATED]


def test_changed_zh_section_emits_only_target_language_mismatch():
    source = language_document('Find all positive integers satisfying the condition.')
    translated = language_document('求出 all positive integers，并证明结论。')
    findings = verify_translation(source, translated, target_lang='zh')
    assert [finding.type for finding in findings] == [
        FindingType.TARGET_LANGUAGE_MISMATCH,
    ]


def test_english_untranslated_contract_remains_on_master_paths():
    french = language_document('Pour simplifier, comparons les deux membres.')
    english = language_document('Find all positive integers.')
    changed = language_document('Сравните две стороны и сделайте вывод.')
    placeholder = language_document('（数据集未提供 / 证明题）', '解法 1')
    mojibake = language_document('Compare the two quantitiÃ©s.')

    assert [finding.type for finding in verify_translation(
        french, french, target_lang='en', source_lang='fr'
    )] == [FindingType.UNTRANSLATED]
    assert verify_translation(
        english, english, target_lang='en', source_lang='en'
    ) == []
    assert verify_translation(english, changed, target_lang='en') == []
    assert [finding.type for finding in verify_translation(
        placeholder, placeholder, target_lang='en', source_lang='zh'
    )] == [FindingType.UNTRANSLATED]
    assert verify_translation(english, mojibake, target_lang='en') == []


def test_plain_prose_strips_html_meta_key_math_and_punctuation():
    body = '- **Case**: <span>**Pour** simplifier, comparons.</span> {{MNT_0001}} $x$ !!!'
    assert plain_prose(body) == 'Pour simplifier comparons'


def test_plain_prose_strips_fenced_code_before_counting_residual_prose():
    body = '中文开头\n```text\nNO SOLUTION\n```\n中文结论'
    assert plain_prose(body) == '中文开头 中文结论'


def test_pure_symbol_whitelist_and_command_stripping_are_independently_live():
    assert is_pure_symbol('gcd')
    assert is_pure_symbol('pi')
    assert is_pure_symbol(r'\operatorname{x}')
    assert not is_pure_symbol('regular polygon')


def test_final_answer_pure_symbol_exemption_is_heading_specific():
    answer = r'\sqrt{5}/125'
    assert not has_translatable_prose(answer, '最终答案')
    assert has_translatable_prose(answer, '题面')


def test_single_letters_and_proper_name_connectors_are_exempt():
    assert language_rules._residual_latin_letters('连接 A 与 B，并使用 gcd。') == 0
    assert language_rules._residual_latin_letters('连接 x 与 y。') == 0
    for connector in ('de', 'van', 'von'):
        assert language_rules._residual_latin_letters(
            f'由 Paul {connector} Erdős 的结论可知。'
        ) == 0
    assert language_rules._residual_latin_letters('把 regular polygon 分成三角形。') > 0
    assert language_rules._residual_latin_letters('de regular') == len('deregular')


def test_capitalized_english_prose_is_not_treated_as_a_proper_name():
    assert language_rules._residual_latin_letters('NO SOLUTION') == len('NOSOLUTION')
    assert target_language_mismatch('中文结论 NO SOLUTION', LanguageConfig())


@pytest.mark.parametrize('body', [
    '（数据集未提供）',
    '（数据集未提供 / 证明题）',
])
def test_known_generator_placeholders_are_not_translatable_prose(body):
    assert not has_translatable_prose(body, '解法 1')


def test_parenthesized_real_solution_is_not_treated_as_generator_placeholder():
    assert has_translatable_prose('（将两边平方得到结论）', '解法 1')


def test_language_findings_skip_target_section_when_source_has_no_prose():
    source = language_document('（数据集未提供 / 证明题）', '解法 1')
    translated = language_document('This generated section must be ignored.', '解法 1')
    findings = verify_translation(source, translated, target_lang='zh')
    assert not {
        FindingType.UNTRANSLATED,
        FindingType.TARGET_LANGUAGE_MISMATCH,
        FindingType.MOJIBAKE,
    }.intersection(finding.type for finding in findings)


def test_short_latin_section_falls_back_to_document_ratio():
    source = (
        '# constructed-short-section\n\n'
        '## 题面\n\nLet positive reals satisfy the relation.\n\n'
        '## 解法 1\n\nProve the conclusion from the stated relation.\n'
    )
    translated = (
        '# constructed-short-section\n\n'
        '## 题面\n\n正实数满足 relation，证明该结论。\n\n'
        '## 解法 1\n\n先整理条件，再逐项比较两边，最后得到所需结论。\n'
    )
    assert verify_translation(source, translated, target_lang='zh') == []


@pytest.mark.parametrize('body', [
    '中文结论中残留 Это 西里尔文字。',
    '中文结论中残留 λέξη 希腊文字。',
])
def test_foreign_script_early_return_is_live(body):
    assert target_language_mismatch(body, LanguageConfig())


def test_mojibake_finding_and_marker_threshold_are_live():
    source = language_document('Compare the two quantities.')
    translated = language_document('比较两边后得到 Ã©。')
    assert [finding.type for finding in verify_translation(
        source, translated, target_lang='zh'
    )] == [FindingType.MOJIBAKE]

    markers = 'Ç\u0080È\u0081É\u0082'
    assert not language_rules.looks_mojibake(markers[:4], LanguageConfig())
    assert language_rules.looks_mojibake(markers, LanguageConfig())


@pytest.mark.parametrize('body', [
    '出现替换字符 �。',
    '错误破折号 â€” 出现在句中。',
    '错误不等号 â‰¤ 出现在句中。',
    '错误乘号 Ã— 出现在句中。',
])
def test_common_windows_1252_and_replacement_mojibake_is_detected(body):
    assert language_rules.looks_mojibake(body, LanguageConfig())


def test_chinese_ratio_and_short_english_thresholds_are_live():
    config = LanguageConfig()
    assert not target_language_mismatch('!!!', config)
    assert target_language_mismatch('求 find', config)
    assert not target_language_mismatch('求 abcdef', config)
    assert not target_language_mismatch('中文 abcdefg', config)
    assert target_language_mismatch('中文 abcdefgh', config)


@pytest.mark.parametrize('kwargs', [
    {'zh_max_latin_ratio': -0.01},
    {'zh_max_latin_ratio': 1.01},
    {'zh_min_latin_letters': 0},
    {'zh_short_section_latin_letters': 0},
    {'zh_min_foreign_script_letters': 0},
    {'mojibake_min_markers': 0},
])
def test_language_config_rejects_dead_thresholds(kwargs):
    with pytest.raises(ValueError):
        verify_translation(
            SOURCE,
            VALID_TRANSLATION,
            language_config=LanguageConfig(**kwargs),
        )


def normal_batch(size=97):
    objects = ('sum', 'product', 'remainder', 'perimeter', 'area', 'maximum', 'minimum',
               'coefficient', 'root', 'divisor')
    methods = ('factoring', 'induction', 'symmetry', 'counting', 'reflection', 'substitution',
               'invariance', 'parity', 'recursion', 'comparison')
    object_zh = ('和', '积', '余数', '周长', '面积', '最大值', '最小值', '系数', '根', '因数')
    method_zh = ('因式分解', '归纳法', '对称性', '计数法', '反射法', '代换法',
                 '不变量', '奇偶性', '递推法', '比较法')
    rows = []
    for index in range(size):
        left, right = index % 10, index // 10
        rows.append({
            'mathnet_id': f'normal-{index:03d}',
            'source': (f'Find the {objects[left]} using {methods[right]} in case '
                       f'{index + 100}.'),
            'translated': (f'在编号 {index + 100} 的情形中，用{method_zh[right]}'
                           f'求{object_zh[left]}。'),
        })
    return rows


def batch_report(rows, **kwargs):
    return verify_batch(
        [row['source'] for row in rows],
        [row['translated'] for row in rows],
        keys=[row.get('mathnet_id') or row['fixture_id'] for row in rows],
        **kwargs,
    )


def test_human_reported_unit_excerpts_block_all_three_boilerplate_translations():
    rows = human_reported_degenerate_fixture()['rows']
    report = batch_report(rows)

    assert set(report.findings) == {row['fixture_id'] for row in rows}
    assert all(
        {finding.type for finding in findings} == {FindingType.BATCH_BOILERPLATE}
        for findings in report.findings.values()
    )


def test_three_reported_excerpts_in_constructed_hundred_only_block_those_three():
    bad = human_reported_degenerate_fixture()['rows']
    rows = normal_batch() + bad
    report = batch_report(rows)

    assert set(report.findings) == {row['fixture_id'] for row in bad}
    assert not (set(report.findings) & {row['mathnet_id'] for row in rows[:97]})


def test_constructed_normal_batch_has_zero_findings():
    assert batch_report(normal_batch()).findings == {}


def test_constructed_proxy_for_reported_zh_batch_blocks_one_hundred_of_one_hundred():
    """固定 100/100 能力；不把三条 excerpt 的机械扩展冒充实际 100 条语料。"""
    fixture = human_reported_degenerate_fixture()
    rows = []
    for index in range(fixture['human_reported_batch']['size']):
        exemplar = fixture['rows'][index % len(fixture['rows'])]
        rows.append({
            'mathnet_id': f'constructed-batch-proxy-{index:03d}',
            'source': exemplar['source'],
            'translated': exemplar['translated'],
        })

    report = batch_report(rows)

    assert fixture['human_reported_batch']['expected_blocked'] == 100
    assert len(report.findings) == 100
    assert all(
        FindingType.BATCH_BOILERPLATE in {finding.type for finding in findings}
        for findings in report.findings.values()
    )


def _render_feature_documents(row, degraded_solution_text):
    source_parts = [f"# {row['mathnet_id']}", '', '## 题面', row['source_statement_excerpt']]
    target_parts = [
        f"# {row['mathnet_id']}", '', '## 题面', row['translated_statement_excerpt']
    ]
    for section in row['solution_sections']:
        source_parts.extend([
            '',
            f"## {section['heading']}",
            section['detector_proxy_discriminator'] + ' ' + section['source_proxy'],
        ])
        target_parts.extend(['', f"## {section['heading']}", section['translated']])
        assert section['translated'] == degraded_solution_text
    source_parts.extend(['', '## 最终答案', '[NOT INCLUDED IN CXB-513 FIXTURE]'])
    target_parts.extend(['', '## 最终答案', '[NOT INCLUDED IN CXB-513 FIXTURE]'])
    return '\n'.join(source_parts) + '\n', '\n'.join(target_parts) + '\n'


def test_cxb_513_human_reported_full_document_features_still_block_all_three():
    fixture = full_document_feature_fixture()
    assert fixture['fixture_kind'].startswith('constructed detector documents')
    rows = []
    for row in fixture['rows']:
        source, translated = _render_feature_documents(
            row, fixture['degraded_solution_text']
        )
        rows.append({
            'mathnet_id': row['mathnet_id'],
            'source': source,
            'translated': translated,
        })

    report = batch_report(rows, target_lang='en')

    assert set(report.findings) == {'096f', '0a3h', '097v'}
    assert len(fixture['rows'][1]['solution_sections']) == 2
    assert all(
        FindingType.BATCH_BOILERPLATE in {finding.type for finding in findings}
        for findings in report.findings.values()
    )


def test_full_pair_audit_records_denominators_and_constructed_normal_examples():
    fixture = audit_fixture()
    reaudit = fixture['human_reaudit']
    assert reaudit['sample_size'] == 3
    assert reaudit['false_positives'] == 0
    assert reaudit['true_degenerations'] == 3
    assert reaudit['false_positive_rate'] == 0.0
    assert 'not a corpus-wide estimate' in reaudit['scope_note']

    by_language = {}
    individual_findings = {}
    for row in fixture['constructed_normal_pairs']:
        source = (FIXTURES / row['source_file']).read_text(encoding='utf-8')
        translated = (FIXTURES / row['translated_file']).read_text(encoding='utf-8')
        headings = [
            line.removeprefix('## ') for line in source.splitlines()
            if line.startswith('## ')
        ]
        assert headings == row['reviewed_sections']
        individual_findings[row['fixture_id']] = verify_translation(
            source,
            translated,
            target_lang=row['target_lang'],
            source_lang='fr' if row['source_file'] == 'mixed-source.md' else 'en',
        )
        by_language.setdefault(row['target_lang'], []).append({
            'mathnet_id': row['fixture_id'],
            'source': source,
            'translated': translated,
        })
    findings = {
        key: finding
        for target_lang, rows in by_language.items()
        for key, finding in batch_report(rows, target_lang=target_lang).findings.items()
    }
    normal_rate = fixture['constructed_normal_false_positive_rate']
    assert normal_rate['sample_size'] == 3
    assert normal_rate['false_positives'] == 0
    assert normal_rate['rate'] == 0.0
    assert 'constructed' in normal_rate['scope_note'].casefold()
    assert individual_findings == {
        row['fixture_id']: [] for row in fixture['constructed_normal_pairs']
    }
    assert findings == {}


def test_constructed_same_genre_cluster_compares_target_and_source_to_same_peer():
    translated = 'Prove that if the real numbers satisfy the stated relations, the result follows.'
    rows = [
        {
            'mathnet_id': 'constructed-near-1',
            'source': 'Să se arate că dacă numerele reale satisfac relațiile date, concluzia rezultă.',
            'translated': translated,
        },
        {
            'mathnet_id': 'constructed-near-2',
            'source': 'Să se arate că dacă numere reale satisfac relațiile date, atunci concluzia rezultă.',
            'translated': translated,
        },
        {
            'mathnet_id': 'constructed-outsider',
            'source': 'Bewijs dat de gegeven meetkundige configuratie de vereiste eigenschap heeft.',
            'translated': translated,
        },
    ]

    assert batch_report(rows, target_lang='en').findings == {}


def test_constructed_paired_source_templates_do_not_punch_through_boilerplate_cluster():
    translated = 'The requested result follows from the stated conditions.'
    sources = (
        'Find the maximum value of the function under the stated algebraic constraints.',
        'Find the minimum value of the function under the stated algebraic constraints.',
        'Prove that the two circles in the stated geometric configuration are tangent.',
        'Prove that the two circles in the stated geometric configuration are orthogonal.',
    )
    rows = [
        {
            'mathnet_id': f'constructed-paired-template-{index}',
            'source': source,
            'translated': translated,
        }
        for index, source in enumerate(sources, start=1)
    ]

    report = batch_report(rows, target_lang='en')

    assert set(report.findings) == {row['mathnet_id'] for row in rows}
    assert all(
        FindingType.BATCH_BOILERPLATE in {finding.type for finding in findings}
        for findings in report.findings.values()
    )
    assert all(
        '簇级跨模板源文相似度' in report.signals[row['mathnet_id']][0].detail
        for row in rows
    )


def test_echoing_one_distinctive_number_does_not_exempt_boilerplate_cluster():
    sources = (
        'Demonstrați teorema geometrică despre cercuri și tangente în cazul 101.',
        'Bestimmen Sie alle ganzzahligen Lösungen des Teilbarkeitsproblems 202.',
        'Calcolare il massimo della funzione soggetta ai vincoli del caso 303.',
        'Trouver le nombre de colorations du graphe décrit dans le cas 404.',
        'Poišči vse polinome, ki izpolnjujejo zahtevane pogoje v primeru 505.',
    )
    rows = [
        {
            'mathnet_id': f'constructed-entity-{index}',
            'source': source,
            'translated': f'The requested result follows from the stated conditions in case {number}.',
        }
        for index, (source, number) in enumerate(
            zip(sources, ('101', '202', '303', '404', '505')), start=1
        )
    ]

    report = batch_report(rows, target_lang='en')

    assert set(report.findings) == {row['mathnet_id'] for row in rows}
    assert all(
        FindingType.BATCH_BOILERPLATE in {finding.type for finding in findings}
        for findings in report.findings.values()
    )


def test_length_ratio_is_a_signal_but_never_blocks_alone():
    source = 'Consider the complete configuration and all of its stated restrictions carefully.'
    report = verify_batch([source], ['简答'], keys=['short'])

    assert report.findings == {}
    assert [signal.type for signal in report.signals['short']] == [FindingType.LENGTH_RATIO]


def test_length_and_missing_anchor_block_as_separate_findings():
    source = 'Find the unique integer satisfying every one of the listed divisibility restrictions.'
    report = verify_batch([source], ['整数'], keys=['hollow'])

    assert {finding.type for finding in report.findings['hollow']} == {
        FindingType.LENGTH_RATIO,
        FindingType.CONTENT_ANCHOR_MISSING,
    }


def test_chinese_source_hollow_english_translation_is_blocked():
    source = '证明对于所有满足下列整除条件的正整数，欧拉方法给出的结论在 2024 年情形下成立。'
    report = verify_batch([source], ['Answer.'], keys=['hollow-en'], target_lang='en')

    assert {finding.type for finding in report.findings['hollow-en']} == {
        FindingType.LENGTH_RATIO,
        FindingType.CONTENT_ANCHOR_MISSING,
    }
    assert '证明' in report.signals['hollow-en'][1].detail


def test_non_english_source_hollow_chinese_translation_is_blocked():
    source = ('Démontrer que pour tous les entiers positifs satisfaisant les conditions données, '
              "l'identité d'Euler reste valable en 2024.")
    report = verify_batch([source], ['答案。'], keys=['hollow-zh'], target_lang='zh')

    assert {finding.type for finding in report.findings['hollow-zh']} == {
        FindingType.LENGTH_RATIO,
        FindingType.CONTENT_ANCHOR_MISSING,
    }
    assert 'demontrer' in report.signals['hollow-zh'][1].detail


def test_content_anchor_axis_still_runs_for_english_targets():
    report = verify_batch(
        ['证明欧拉结论在 2024 年成立。'],
        ['Answer.'],
        keys=['en-anchor-axis'],
        target_lang='en',
        config=BatchConfig(length_weight=0, anchor_weight=2),
    )

    assert {finding.type for finding in report.findings['en-anchor-axis']} == {
        FindingType.CONTENT_ANCHOR_MISSING,
    }


@pytest.mark.parametrize('source', [
    'Démontrer que la propriété demandée vaut pour tous les entiers positifs.',
    'Bestimmen Sie alle ganzen Zahlen, welche die angegebenen Bedingungen erfüllen.',
    'Demostrar que la identidad dada es válida para todos los números reales.',
    'Calcolare il valore massimo soggetto alle condizioni indicate.',
    'Poišči vsa pozitivna cela števila, ki izpolnjujejo dane pogoje.',
])
def test_constructed_multilingual_task_anchor_regression_blocks_five_of_five(source):
    report = verify_batch([source], ['答案。'], target_lang='zh')

    assert {finding.type for finding in report.findings['0']} == {
        FindingType.LENGTH_RATIO,
        FindingType.CONTENT_ANCHOR_MISSING,
    }


@pytest.mark.parametrize(('source', 'translated', 'target_lang'), [
    (
        '证明欧拉方法给出的结论在 2024 年情形下成立。',
        "Prove that Euler's method gives the claimed result in the 2024 case.",
        'en',
    ),
    (
        "Démontrer que l'identité d'Euler reste valable en 2024.",
        '证明欧拉恒等式在 2024 年仍然成立。',
        'zh',
    ),
])
def test_cross_language_content_anchors_accept_normal_translations(
    source, translated, target_lang
):
    assert verify_batch([source], [translated], target_lang=target_lang).findings == {}


def test_batch_thresholds_are_configurable():
    rows = human_reported_degenerate_fixture()['rows']
    report = batch_report(rows, config=BatchConfig(boilerplate_min_group=4))
    assert report.findings == {}


def test_pair_similarity_tolerance_is_validated():
    rows = human_reported_degenerate_fixture()['rows']
    with pytest.raises(ValueError, match='boilerplate_pair_similarity_tolerance'):
        batch_report(rows, config=BatchConfig(boilerplate_pair_similarity_tolerance=-0.1))


@pytest.mark.parametrize(('old', 'new'), [
    (r'$x \leq y$', r'$x \geq y$'),                  # 历史事故：不等号反转
    (r'$x=4$', r'$x=\frac{4}{1}$'),                  # 看似等价仍禁止改写
    (r'$y=6$', r'$y=6\;$'),                          # 空白命令被增删
    ('x + y = 10', r'\frac{x+y}{1}=10'),             # frac/差商类改写
    ('`x + y`', '`x+y`'),                             # 行内代码也逐字保真
    ('x-y &= 2', 'x－y &= 2'),                        # 全角符号替换
])
def test_math_and_inline_code_changes_are_rejected(old, new):
    changed = VALID_TRANSLATION.replace(old, new)
    assert FindingType.MATH_MISMATCH in types(translated=changed)


def test_identical_math_and_code_are_accepted():
    assert FindingType.MATH_MISMATCH not in types()


def test_cxb_525_placeholder_pipeline_allows_math_multiset_reordering_only():
    source, translated = placeholder_reordering_documents()

    assert FindingType.MATH_MISMATCH in types(source, translated)
    assert verify_translation(
        source,
        translated,
        target_lang='zh',
        source_lang='en',
        placeholder_pipeline=True,
    ) == []

    changed = translated.replace(r'$a_1+\cdots+a_n \ne 0$', r'$a_1+\cdots+a_n = 0$')
    assert FindingType.MATH_MISMATCH in types(
        source,
        changed,
        placeholder_pipeline=True,
    )


def test_frac_changed_to_slash_is_rejected():
    source = SOURCE.replace('$x=4$', r'$\frac{x}{2}=2$')
    translated = VALID_TRANSLATION.replace('$x=4$', '$x/2=2$')
    assert FindingType.MATH_MISMATCH in types(source, translated)


def test_image_number_and_order_drift_are_rejected():
    second_source = SOURCE.replace(
        '![](attached_image_1.png)',
        '![](attached_image_1.png)\n![](attached_image_2.png)',
    )
    swapped = VALID_TRANSLATION.replace(
        '![](attached_image_1.png)',
        '![](attached_image_2.png)\n![](attached_image_1.png)',
    )
    findings = verify_translation(second_source, swapped)
    assert any(finding.type == FindingType.IMAGE_MISMATCH for finding in findings)
    assert any(finding.section == '题面' for finding in findings)


def test_identical_image_references_are_accepted():
    assert FindingType.IMAGE_MISMATCH not in types()


@pytest.mark.parametrize('changed', [
    VALID_TRANSLATION.replace('# sample-001', '# sample-002'),
    VALID_TRANSLATION.replace('- contest:', '- event:'),
    VALID_TRANSLATION.replace('## 解法 1\n令 $x=4$，并验证等式。\n\n', ''),  # 合并/减少解法
    VALID_TRANSLATION.replace('## 解法 2', '## 解法 3'),
])
def test_h1_metadata_and_section_skeleton_drift_are_rejected(changed):
    assert FindingType.STRUCTURE_MISMATCH in types(translated=changed)


def test_identical_skeleton_is_accepted():
    assert FindingType.STRUCTURE_MISMATCH not in types()


@pytest.mark.parametrize(('answer', 'replacement'), [
    ('D', 'B'),
    ('D', '1'),
])
def test_pure_symbol_final_answer_must_be_identical(answer, replacement):
    changed = VALID_TRANSLATION.rsplit(answer, 1)[0] + replacement + '\n'
    assert FindingType.FINAL_ANSWER_MISMATCH in types(translated=changed)


@pytest.mark.parametrize('answer', ['1', 'D', 'sqrt(5)/125', 'n ≡ 1 (mod 3)', r'$\frac{1}{2}$'])
def test_unchanged_pure_symbol_answer_is_accepted(answer):
    source = SOURCE.rsplit('D\n', 1)[0] + answer + '\n'
    translated = VALID_TRANSLATION.rsplit('D\n', 1)[0] + answer + '\n'
    findings = verify_translation(source, translated)
    assert not any(finding.type in {FindingType.FINAL_ANSWER_MISMATCH, FindingType.UNTRANSLATED}
                   and finding.section == '最终答案' for finding in findings)


@pytest.mark.parametrize('answer', ['sqrt(5)/125', 'D', '1'])
@pytest.mark.parametrize(('target_lang', 'fixture'), [('en', 'mixed-en.md'), ('zh', 'mixed-zh.md')])
def test_symbolic_final_answer_is_identical_across_three_versions(answer, target_lang, fixture):
    prose_answer = 'Equality holds if and only if x equals y.'
    source = (FIXTURES / 'mixed-source.md').read_text(encoding='utf-8').replace(
        prose_answer, answer
    )
    translated = (FIXTURES / fixture).read_text(encoding='utf-8').replace(prose_answer, answer)

    assert verify_translation(
        source, translated, target_lang=target_lang, source_lang='fr'
    ) == []


def test_empty_section_and_empty_document_are_rejected():
    empty_section = VALID_TRANSLATION.replace('令 $x=4$，并验证等式。', '')
    section_findings = verify_translation(SOURCE, empty_section)
    assert any(f.type == FindingType.EMPTY_TRANSLATION and f.section == '解法 1'
               and 'Set $x=4$' in f.source_excerpt
               for f in section_findings)
    assert types(translated='') >= {FindingType.EMPTY_TRANSLATION, FindingType.STRUCTURE_MISMATCH}


def test_identical_translated_section_is_rejected_but_passthrough_is_allowed():
    translated = VALID_TRANSLATION.replace('求整数 $x \\leq y$，使 `x + y` 为偶数。',
                                           'Find integers $x \\leq y$ such that `x + y` is even.')
    assert FindingType.UNTRANSLATED in types(translated=translated, mode='translated')
    assert verify_translation(SOURCE, SOURCE, mode='passthrough') == []


def test_proof_placeholder_is_preserved_across_all_three_versions():
    source = (FIXTURES / 'proof-source.md').read_text(encoding='utf-8')
    english = (FIXTURES / 'proof-en.md').read_text(encoding='utf-8')
    chinese = (FIXTURES / 'proof-zh.md').read_text(encoding='utf-8')

    assert verify_translation(source, english, mode='passthrough', target_lang='en') == []
    assert verify_translation(source, chinese, target_lang='zh') == []


@pytest.mark.parametrize('answer', [
    '（数据集未提供 / 证明题）',
    '此题为证明题，未单列答案。',
])
def test_identical_chinese_source_section_needs_no_translation(answer):
    source = SOURCE.replace('D\n', answer + '\n')
    translated = VALID_TRANSLATION.replace('D\n', answer + '\n')

    assert FindingType.UNTRANSLATED not in types(source, translated, target_lang='zh')


def test_source_section_already_in_english_needs_no_translation():
    source = (FIXTURES / 'mixed-source.md').read_text(encoding='utf-8')
    translated = (FIXTURES / 'mixed-en.md').read_text(encoding='utf-8')

    assert verify_translation(
        source, translated, target_lang='en', source_lang='fr'
    ) == []


@pytest.mark.parametrize('answer', [
    'No solutions.',
    'Proof omitted.',
    'The answer is 42.',
])
def test_short_english_answer_needs_no_translation(answer):
    source = SOURCE.replace('D\n', answer + '\n')
    translated = VALID_TRANSLATION.replace('D\n', answer + '\n')

    findings = verify_translation(
        source, translated, target_lang='en', source_lang='fr'
    )

    assert not any(
        finding.type == FindingType.UNTRANSLATED and finding.section == '最终答案'
        for finding in findings
    )


@pytest.mark.parametrize('answer', [
    'Aucune solution.',
    'Nessuna soluzione.',
    'Keine Lösungen.',
])
def test_short_foreign_answer_is_not_trusted_as_english(answer):
    source = SOURCE.replace('D\n', answer + '\n')
    translated = VALID_TRANSLATION.replace('D\n', answer + '\n')

    findings = verify_translation(
        source, translated, target_lang='en', source_lang='en'
    )

    assert any(
        finding.type == FindingType.UNTRANSLATED and finding.section == '最终答案'
        for finding in findings
    )


def test_matching_file_language_does_not_exempt_mixed_language_section():
    french_solution = "On compare les deux membres de l'égalité."
    source = SOURCE.replace('Set $x=4$ and verify the equality.', french_solution)
    translated = VALID_TRANSLATION.replace('令 $x=4$，并验证等式。', french_solution)

    findings = verify_translation(
        source, translated, target_lang='en', source_lang='en'
    )

    assert any(
        finding.type == FindingType.UNTRANSLATED and finding.section == '解法 1'
        for finding in findings
    )


def test_real_prose_left_identical_is_still_untranslated():
    untranslated = VALID_TRANSLATION.replace(
        '求整数 $x \\leq y$，使 `x + y` 为偶数。',
        'Find integers $x \\leq y$ such that `x + y` is even.',
    )

    findings = verify_translation(SOURCE, untranslated, target_lang='zh')
    assert any(finding.type == FindingType.UNTRANSLATED and finding.section == '题面'
               for finding in findings)


def test_passthrough_identical_document_has_zero_findings():
    assert verify_translation(SOURCE, SOURCE, mode='passthrough', target_lang='en') == []
    assert verify_translation(
        SOURCE,
        translated=SOURCE,
        mode='translated',
        target_lang='en',
        source_lang='en',
    ) == []


def test_foreign_prose_left_identical_in_english_variant_is_untranslated():
    source = (FIXTURES / 'mixed-source.md').read_text(encoding='utf-8')
    translated = (FIXTURES / 'mixed-en.md').read_text(encoding='utf-8').replace(
        'Find the real numbers $x$ and $y$ satisfying the equality.',
        "Trouver les réels $x$ et $y$ satisfaisant l'égalité.",
    )

    findings = verify_translation(
        source, translated, target_lang='en', source_lang='fr'
    )
    assert any(finding.type == FindingType.UNTRANSLATED and finding.section == '题面'
               for finding in findings)


def test_regression_inequality_reversal_is_rejected():
    changed = VALID_TRANSLATION.replace(r'$x \leq y$', r'$x \geq y$')
    assert FindingType.MATH_MISMATCH in types(translated=changed)


def test_regression_image_number_drift_is_rejected():
    changed = VALID_TRANSLATION.replace('attached_image_1', 'attached_image_2')
    assert FindingType.IMAGE_MISMATCH in types(translated=changed)


def test_regression_deleted_solution_two_is_rejected():
    changed = VALID_TRANSLATION.replace('## 解法 2\n也可以使用 $y=6$。\n\n', '')
    assert FindingType.STRUCTURE_MISMATCH in types(translated=changed)


def test_regression_model_meta_leak_is_rejected():
    changed = VALID_TRANSLATION.replace('求整数', '以下是翻译：\n求整数')
    assert FindingType.MODEL_META_LEAK in types(translated=changed)


def test_adversarial_detection_rates_remain_exact():
    inequality_hits = 0
    for index in range(58):
        old = rf'$x_{{{index}}} \leq y_{{{index}}}$'
        source = SOURCE.replace(r'$x \leq y$', old)
        translated = VALID_TRANSLATION.replace(r'$x \leq y$', old)
        changed = translated.replace(r'\leq', r'\geq', 1)
        inequality_hits += any(
            finding.type == FindingType.MATH_MISMATCH
            for finding in verify_translation(source, changed)
        )

    image_hits = 0
    for index in range(1, 95):
        source = SOURCE.replace('attached_image_1', f'attached_image_{index}')
        translated = VALID_TRANSLATION.replace('attached_image_1', f'attached_image_{index + 1}')
        image_hits += any(
            finding.type == FindingType.IMAGE_MISMATCH
            for finding in verify_translation(source, translated)
        )

    skeleton_hits = 0
    for index in range(33):
        heading = f'解法 {index + 3}'
        source = SOURCE.replace('解法 2', heading)
        translated = VALID_TRANSLATION.replace('解法 2', heading)
        changed = translated.replace(f'## {heading}\n也可以使用 $y=6$。\n\n', '')
        skeleton_hits += any(
            finding.type == FindingType.STRUCTURE_MISMATCH
            for finding in verify_translation(source, changed)
        )

    leak_hits = 0
    for index in range(500):
        changed = VALID_TRANSLATION.replace('求整数', f'以下是翻译 {index}：求整数')
        leak_hits += any(
            finding.type == FindingType.MODEL_META_LEAK
            for finding in verify_translation(SOURCE, changed)
        )

    assert (inequality_hits, image_hits, skeleton_hits, leak_hits) == (58, 94, 33, 500)


@pytest.mark.parametrize('phrase', [
    '以下是翻译：',
    'Here is the translation:',
    '作为一个 AI，我会翻译。',
])
def test_model_meta_language_is_rejected(phrase):
    changed = VALID_TRANSLATION.replace('求整数', phrase + '\n求整数')
    assert FindingType.MODEL_META_LEAK in types(translated=changed)


def test_whole_document_code_fence_is_rejected_but_inner_code_is_allowed():
    fenced = '```markdown\n' + VALID_TRANSLATION + '```'
    assert FindingType.EXTRA_CODE_FENCE in types(translated=fenced)
    assert FindingType.EXTRA_CODE_FENCE not in types()


def test_directory_cli_reports_passes_and_counts(tmp_path, capsys):
    source_dir = tmp_path / 'source'
    translated_dir = tmp_path / 'translated'
    source_dir.mkdir()
    translated_dir.mkdir()
    (source_dir / 'ok.md').write_text(SOURCE, encoding='utf-8')
    (translated_dir / 'ok.md').write_text(VALID_TRANSLATION, encoding='utf-8')
    (source_dir / 'bad.md').write_text(SOURCE, encoding='utf-8')
    bad = VALID_TRANSLATION.replace(r'$x \leq y$', r'$x \geq y$')
    (translated_dir / 'bad.md').write_text(bad, encoding='utf-8')

    report = verify_directory(source_dir, translated_dir)
    assert (report.total, report.passed, report.failed) == (2, 1, 1)
    assert report.finding_counts[FindingType.MATH_MISMATCH.value] == 1

    assert main([str(source_dir), str(translated_dir), '--json']) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload['passed'] == 1
    assert payload['finding_counts']['math_mismatch'] == 1


def test_directory_reports_missing_translation(tmp_path):
    source_dir = tmp_path / 'source'
    translated_dir = tmp_path / 'translated'
    source_dir.mkdir()
    translated_dir.mkdir()
    (source_dir / 'missing.md').write_text(SOURCE, encoding='utf-8')
    report = verify_directory(source_dir, translated_dir)
    assert report.finding_counts == {FindingType.MISSING_TRANSLATION.value: 1}


def test_only_translated_skips_missing_variants(tmp_path, capsys):
    translated_problem = tmp_path / 'translated' / 'sample-001'
    untranslated_problem = tmp_path / 'untranslated' / 'sample-002'
    translated_problem.mkdir(parents=True)
    untranslated_problem.mkdir(parents=True)
    (translated_problem / 'index.md').write_text(SOURCE, encoding='utf-8')
    (translated_problem / 'index.zh.md').write_text(VALID_TRANSLATION, encoding='utf-8')
    (untranslated_problem / 'index.md').write_text(SOURCE, encoding='utf-8')

    strict = verify_directory(tmp_path, variant='zh')
    existing = verify_directory(tmp_path, variant='zh', only_translated=True)
    assert (strict.total, strict.failed) == (2, 1)
    assert strict.finding_counts == {FindingType.MISSING_TRANSLATION.value: 1}
    assert (existing.total, existing.passed, existing.failed) == (1, 1, 0)
    assert main([str(tmp_path), '--variant', 'zh', '--only-translated']) == 0
    assert '共 1 题；通过 1；失败 0' in capsys.readouterr().out


def test_directory_rejects_missing_source_root(tmp_path):
    with pytest.raises(FileNotFoundError, match='source directory does not exist'):
        verify_directory(tmp_path / 'absent')


def test_single_corpus_directory_pairs_contract_filenames(tmp_path, capsys):
    problem_dir = tmp_path / 'by-topic' / 'algebra' / 'sample-001'
    problem_dir.mkdir(parents=True)
    (problem_dir / 'index.md').write_text(SOURCE, encoding='utf-8')
    (problem_dir / 'index.zh.md').write_text(VALID_TRANSLATION, encoding='utf-8')

    report = verify_directory(tmp_path, variant='zh')
    assert (report.total, report.passed, report.failed) == (1, 1, 0)
    assert main([str(tmp_path), '--variant', 'zh']) == 0
    assert '通过 1；失败 0' in capsys.readouterr().out
