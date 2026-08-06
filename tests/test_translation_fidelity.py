"""译文保真校验器的自造配对样本；不依赖 mathnet-full/，不触碰 problems/。"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from translation_fidelity import FindingType, main, verify_directory, verify_translation  # noqa: E402


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


@pytest.mark.parametrize('answer', ['1', 'D', 'n ≡ 1 (mod 3)', r'$\frac{1}{2}$'])
def test_unchanged_pure_symbol_answer_is_accepted(answer):
    source = SOURCE.rsplit('D\n', 1)[0] + answer + '\n'
    translated = VALID_TRANSLATION.rsplit('D\n', 1)[0] + answer + '\n'
    findings = verify_translation(source, translated)
    assert not any(finding.type in {FindingType.FINAL_ANSWER_MISMATCH, FindingType.UNTRANSLATED}
                   and finding.section == '最终答案' for finding in findings)


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
