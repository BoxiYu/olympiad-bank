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


def degenerate_fixture():
    return json.loads((FIXTURES / 'degenerate-batch.json').read_text(encoding='utf-8'))


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
        keys=[row['mathnet_id'] for row in rows],
        **kwargs,
    )


def test_real_degenerate_fixture_blocks_all_three_boilerplate_translations():
    rows = degenerate_fixture()
    report = batch_report(rows)

    assert set(report.findings) == {row['mathnet_id'] for row in rows}
    assert all(
        {finding.type for finding in findings} == {FindingType.BATCH_BOILERPLATE}
        for findings in report.findings.values()
    )


def test_three_degenerate_items_in_one_hundred_only_block_those_three():
    bad = degenerate_fixture()
    rows = normal_batch() + bad
    report = batch_report(rows)

    assert set(report.findings) == {row['mathnet_id'] for row in bad}
    assert not (set(report.findings) & {row['mathnet_id'] for row in rows[:97]})


def test_normal_batch_has_zero_findings():
    assert batch_report(normal_batch()).findings == {}


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
    report = batch_report(degenerate_fixture(), config=BatchConfig(boilerplate_min_group=4))
    assert report.findings == {}


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
