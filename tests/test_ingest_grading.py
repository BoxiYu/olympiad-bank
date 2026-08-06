"""MathNet 候选池「估级与映射」纯函数用例（纯 stdlib + pytest，不读数据集、不联网、不写仓库）。

覆盖 scripts/mathnet_ingest.py 的：
  norm_comp / comp_variants / key_form / family_match / compile_modifiers / grade
  / make_path_normalizer / selfcheck

回归锚点（2026-08-03 跨模型评审暴露的「赛事难度表匹配三连败」）：
  a) RMM 家族整条缺失 → 'seventeenth romanian master of mathematics' 曾被判 ★2（实际 ★5）
  b) 英文拼写的届数序数没剥（'tenth philippine…'），但句中 'third round' 是轮次信息必须保留
  c) 表键带 'the ' 前缀而输入不带（'problems of ukrainian authors'）双向不匹配

运行：uv run --group dev pytest -q
"""
import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
import mathnet_ingest as m  # noqa: E402


# ---------------- 公共构造件：合成 tier 表 / 修正器 ----------------

def prepare_tiers(spec):
    """把 {赛名: base} 合成成 build() 里那份 tiers（含 _by_keyform）与 tier_keys_by_len。

    必须与 scripts/mathnet_ingest.py build() 的构造方式逐行一致，否则用例测的不是生产路径。
    """
    tiers = {name: {'base': base} for name, base in spec.items()}
    keys_by_len = sorted([k for k in tiers if not k.startswith('_')], key=len, reverse=True)
    tiers['_by_keyform'] = {m.key_form(k): k for k in keys_by_len if m.key_form(k) != k}
    return tiers, keys_by_len


def _keys(*names):
    """family_match 约定入参已按长度降序（最长表内整词优先）。"""
    return sorted(names, key=len, reverse=True)


# 与 taxonomy/contest_tiers.yml 同构的修正器片段（合成，便于逐条断言语义）
MOD_SPECS = {
    'circle': {'pattern': r'(?i)(math circle|monthly contest)', 'effect': '直接定为2'},
    'imo': {'pattern': r'(?i)\b(imo)\b(?!.*(junior|prelim|first|booklet|training))',
            'effect': '直接定为4'},
    'tst': {'pattern': r'(?i)\b(tst|team selection|selection test)\b', 'effect': '+1（上限5）'},
    'shortlist': {'pattern': r'(?i)\b(shortlist(ed)?|short list)\b', 'effect': '+1（上限5）'},
    'final': {'pattern': r'(?i)\b(final round|third round|final stage)\b', 'effect': '+1（上限5）'},
    'first': {'pattern': r'(?i)\b(first round|preliminary|qualif\w*)\b', 'effect': '-1（下限1）'},
    'junior': {'pattern': r'(?i)\b(junior|jbmo|cadet)\b', 'effect': '-1（下限1）'},
    'booklet': {'pattern': r'(?i)\b(booklet|training|preparation)\b', 'effect': '-0（保持 default）'},
}


def mods(*names):
    """按给定顺序编译修正器（顺序即求值序：set 命中即终止）。"""
    return m.compile_modifiers([MOD_SPECS[n] for n in names])


DEFAULT = {'base': 2, 'conf': 'low'}


# ---------------- 1. norm_comp ----------------

class TestNormComp:
    def test_strips_year_digit_ordinal_roman_and_punctuation(self):
        """防回归：赛名归一化漏剥四位年份/数字序数/罗马数字/标点，导致同一赛事出现多个键。"""
        assert m.norm_comp('  61st IMO — Shortlist, 2020 ') == 'imo shortlist'
        assert m.norm_comp('XXV Olimpiada de Mayo/Nivel 2') == 'olimpiada de mayo nivel 2'
        assert m.norm_comp('Cono_Sur-Olympiad 1999') == 'cono sur olympiad'

    def test_english_word_ordinal_and_round_words_survive(self):
        """防回归：norm_comp 只剥数字/罗马序数；英文拼写届数与轮次词留给 comp_variants 处理（锚点①b）。"""
        assert m.norm_comp('Seventeenth Romanian Master of Mathematics 2025') == \
            'seventeenth romanian master of mathematics'
        assert m.norm_comp('The South African Mathematical Olympiad Third Round') == \
            'the south african mathematical olympiad third round'

    @pytest.mark.parametrize('raw', [None, '', '   ', '2019', 'XIV', '— , .'])
    def test_empty_or_all_noise_returns_none(self, raw):
        """防回归：空值/只剩噪声的赛名必须返回 None（而不是空串），否则会被当成「已知赛名」走修正器分支。"""
        assert m.norm_comp(raw) is None

    def test_case_and_whitespace_collapsed(self):
        """防回归：大小写与连续空格未压平会造成表内同名键匹配不上。"""
        assert m.norm_comp('  HMMT   February  ') == 'hmmt february'

    def test_year_is_stripped_from_name_but_kept_by_extract_year(self):
        """防回归：年份必须从赛名键里剥掉、同时仍能从原串取到（两个函数各司其职）。"""
        assert m.norm_comp('IMO 2019') == 'imo'
        assert m.extract_year('IMO 2019') == 2019
        assert m.extract_year('IMO') is None


# ---------------- 2. comp_variants / key_form（锚点①b） ----------------

class TestCompVariantsAndKeyForm:
    def test_leading_the_and_word_ordinal_produce_variants_in_order(self):
        """防回归：开头的 the 与英文届数序数必须各产出一个回退写法，且按「原名→去the→去序数」的顺序重试。"""
        assert m.comp_variants('tenth philippine mathematical olympiad') == [
            'tenth philippine mathematical olympiad', 'philippine mathematical olympiad']
        assert m.comp_variants('the danube mathematical competition') == [
            'the danube mathematical competition', 'danube mathematical competition']
        assert m.comp_variants('the sixth iberoamerican olympiad') == [
            'the sixth iberoamerican olympiad', 'sixth iberoamerican olympiad',
            'iberoamerican olympiad']

    @pytest.mark.parametrize('cn', [
        'third round of the national olympiad',
        'second selection test for imo',
        'first stage moldova olympiad',
        'second day problem set',
        'fourth problem session',
    ])
    def test_ordinal_before_round_words_is_never_stripped(self, cn):
        """防回归（锚点①b）：'third round'/'second selection' 等是轮次信息，剥掉会把决赛轮误当正赛。"""
        assert m.comp_variants(cn) == [cn]
        assert m.key_form(cn) == cn

    def test_mid_string_ordinal_is_not_stripped(self):
        """防回归：只剥赛名开头的序数，句中的 'tenth' 属于赛事名的一部分不能动。"""
        assert m.comp_variants('olympiad of the tenth district') == ['olympiad of the tenth district']

    def test_key_form_strips_both_the_and_leading_ordinal(self):
        """防回归（锚点①c 的表键侧）：查找键必须同时抹掉开头 the 与届数序数，才能双向对齐。"""
        assert m.key_form('the tenth philippine mathematical olympiad') == \
            'philippine mathematical olympiad'
        assert m.key_form('the problems of ukrainian authors') == 'problems of ukrainian authors'
        # 轮次名的表键保持原样，否则 'the … third round'（base 已含轮次）会退化成正赛键
        assert m.key_form('the third round selection contest') == 'third round selection contest'

    def test_none_and_empty_inputs(self):
        """防回归：comp_norm 为 None 时不得抛异常，且不产生空字符串变体（空串会误命中 dict 查找）。"""
        assert m.comp_variants(None) == []
        assert m.comp_variants('') == []
        assert m.key_form(None) == ''


# ---------------- 3. family_match ----------------

class TestFamilyMatch:
    def test_longest_table_key_wins(self):
        """防回归：家族回退必须取最长的表内整词子串，否则 'hmmt november' 会退化成泛称 'hmmt'。"""
        keys = _keys('hmmt', 'hmmt november', 'hmmt february')
        assert m.family_match('hmmt november team round', keys) == 'hmmt november'
        assert m.family_match('hmmt invitational competition', keys) == 'hmmt'

    def test_short_key_allowed_only_via_whitelist(self):
        """防回归：短于 8 字符的表键只有白名单缩写能参与家族匹配。"""
        assert 'rmm' in m.FAMILY_SHORT_OK and m.FAMILY_MIN_LEN == 8
        assert m.family_match('rmm day 1', _keys('rmm')) == 'rmm'

    def test_short_generic_key_must_not_match(self):
        """防回归（负例）：'circle'/'cup' 这类短泛词不在白名单，绝不能把校内赛错配到知名赛事档位。"""
        assert m.family_match('berkeley math circle', _keys('circle')) is None
        assert m.family_match('spring cup for schools', _keys('cup')) is None

    def test_min_length_boundary_is_inclusive_at_eight(self):
        """防回归：FAMILY_MIN_LEN 的边界是「短于 8 才拒」，8 字符键仍可匹配。"""
        assert m.family_match('annual zeta cup finals', _keys('zeta cup')) == 'zeta cup'
        assert m.family_match('annual eta cup finals', _keys('eta cup')) is None

    def test_whole_word_matching_only(self):
        """防回归：整词匹配，'imo' 不得命中 'kimo'/'imos' 之类的字母粘连。"""
        keys = _keys('imo')
        assert m.family_match('imo shortlist', keys) == 'imo'
        assert m.family_match('kimo cup', keys) is None
        assert m.family_match('imos national round', keys) is None

    def test_no_match_returns_none(self):
        """防回归：无家族命中必须返回 None，让 grade 落到修正器/default 分支。"""
        assert m.family_match('unknown local contest', _keys('baltic way')) is None


# ---------------- 4. compile_modifiers ----------------

class TestCompileModifiers:
    def test_four_effect_syntaxes(self):
        """防回归：四种 effect 写法各自编译成正确的 (kind, value)，写错会让整表修正器静默失效。"""
        compiled = mods('circle', 'tst', 'junior', 'booklet')
        assert [(k, v, lc) for _rx, k, v, lc in compiled] == [
            ('set', 2, False), ('delta', 1, False), ('delta', -1, False), ('noop', 0, True)]

    def test_set_value_and_regex_are_usable(self):
        """防回归：pattern 必须编译成可 search 的正则，且「直接定为N」取到的是 N 本身。"""
        (rx, kind, val, _lc), = mods('imo')
        assert (kind, val) == ('set', 4)
        assert rx.search('imo shortlist') and not rx.search('imo junior training')

    def test_illegal_effect_raises_valueerror(self):
        """防回归：无法解析的 effect 必须报错而不是被静默忽略（表改坏了要当场炸）。"""
        with pytest.raises(ValueError, match='无法解析 modifier effect'):
            m.compile_modifiers([{'pattern': 'x', 'effect': '×2（乘二）'}])


# ---------------- 5. grade：表内命中 / 家族回退 / 修正器 / default ----------------

class TestGradeLookup:
    def test_exact_tier_hit_is_high_confidence(self):
        """防回归：表内规范名精确命中 → 用表内 base 且 conf=high。"""
        tiers, keys = prepare_tiers({'russian mathematical olympiad': 4})
        assert m.grade('russian mathematical olympiad', None, tiers, mods(), DEFAULT, keys) == (4, 'high')

    def test_family_fallback_is_mid_confidence(self):
        """防回归：长尾写法走家族回退时，档位取表内 base 但置信度必须降到 mid。"""
        tiers, keys = prepare_tiers({'hmmt february': 3})
        assert m.grade('hmmt february guts round', None, tiers, mods(), DEFAULT, keys) == (3, 'mid')

    def test_unknown_name_without_modifier_is_default_low(self):
        """防回归：全未命中时落 default_unknown 的 base，且置信度是 low。"""
        tiers, keys = prepare_tiers({'baltic way': 3})
        assert m.grade('mystery town contest', None, tiers, mods('tst'), DEFAULT, keys) == (2, 'low')

    def test_none_comp_uses_default_conf_from_table(self):
        """防回归：赛名缺失（None）时用 default 的 base+conf，不得进入修正器分支。"""
        tiers, keys = prepare_tiers({'baltic way': 3})
        assert m.grade(None, None, tiers, mods('tst'), {'base': 3, 'conf': 'low'}, keys) == (3, 'low')


class TestGradeModifiers:
    def test_modifier_hit_outside_table_is_mid(self):
        """防回归：表外赛名命中修正器 → 在 default 基础上加减，置信度 mid（不是 low 也不是 high）。"""
        tiers, keys = prepare_tiers({'baltic way': 3})
        assert m.grade('national team selection test', None, tiers, mods('tst'), DEFAULT, keys) == (3, 'mid')

    def test_deltas_accumulate_and_clamp(self):
        """防回归：多条 delta 累加，且上限 5 / 下限 1 必须夹住（越界档位会污染 difficulty_est）。"""
        tiers, keys = prepare_tiers({})
        both = mods('shortlist', 'final')
        assert m.grade('shortlist final round', None, tiers, both, DEFAULT, keys) == (4, 'mid')
        assert m.grade('shortlist final round', None, tiers, both, {'base': 5, 'conf': 'low'}, keys) == (5, 'mid')
        assert m.grade('preliminary qualifying round', None, tiers, mods('first'),
                       {'base': 1, 'conf': 'low'}, keys) == (1, 'mid')

    def test_set_rule_short_circuits_later_deltas(self):
        """防回归：「直接定为N」命中即终止，后面的 +1/-1 不得再叠加。"""
        tiers, keys = prepare_tiers({})
        assert m.grade('math circle shortlist', None, tiers,
                       mods('circle', 'shortlist'), DEFAULT, keys) == (2, 'mid')

    def test_noop_only_keeps_default_base_with_low_conf(self):
        """防回归：'-0' 训练材料规则只降置信度——base 保持 default，conf 仍是 low。"""
        tiers, keys = prepare_tiers({})
        assert m.grade('imo training booklet', None, tiers,
                       mods('imo', 'booklet'), DEFAULT, keys) == (2, 'low')

    def test_tst_plus_one_yields_to_junior_and_first_round(self):
        """防回归：TST 的 +1 在同名含 junior / first round 时不加（否则低龄选拔被抬到高档）。"""
        tiers, keys = prepare_tiers({})
        ms = mods('tst', 'first', 'junior')
        assert m.grade('junior team selection test', None, tiers, ms, DEFAULT, keys) == (1, 'mid')
        assert m.grade('tst first round', None, tiers, ms, DEFAULT, keys) == (1, 'mid')
        assert m.grade('tst final selection', None, tiers, ms, DEFAULT, keys) == (3, 'mid')


class TestGradePtypeCap:
    def test_mcq_capped_at_two_and_final_answer_only_at_three(self):
        """防回归：PTYPE_CAP 必须对选择题封 ★2、只填答案封 ★3。"""
        tiers, keys = prepare_tiers({'imo': 5})
        assert m.PTYPE_CAP == {'MCQ': 2, 'final answer only': 3}
        assert m.grade('imo', 'MCQ', tiers, mods(), DEFAULT, keys) == (2, 'high')
        assert m.grade('imo', 'final answer only', tiers, mods(), DEFAULT, keys) == (3, 'high')

    def test_cap_only_lowers_never_raises(self):
        """防回归（就低不就高）：低于封顶值的档位不得被 PTYPE_CAP 抬上去。"""
        tiers, keys = prepare_tiers({'kangaroo cup': 1})
        assert m.grade('kangaroo cup', 'MCQ', tiers, mods(), DEFAULT, keys) == (1, 'high')

    def test_unknown_problem_type_does_not_cap(self):
        """防回归：证明题/空 problem_type 不触发封顶。"""
        tiers, keys = prepare_tiers({'imo': 5})
        assert m.grade('imo', 'proof', tiers, mods(), DEFAULT, keys) == (5, 'high')
        assert m.grade('imo', None, tiers, mods(), DEFAULT, keys) == (5, 'high')

    def test_cap_keeps_high_but_lifts_lower_conf_to_mid(self):
        """防回归：封顶后的置信度契约——high 保持 high，其余一律记 mid（估级依据已变成题型）。"""
        tiers, keys = prepare_tiers({})
        assert m.grade('unknown quiz', 'MCQ', tiers, mods(), {'base': 4, 'conf': 'low'}, keys) == (2, 'mid')


# ---------------- 6. 回归锚点①：赛事难度表匹配三连败 ----------------

class TestGradingRegressionAnchors:
    def test_a_rmm_family_word_ordinal_reaches_star5(self):
        """锚点①a：'seventeenth romanian master of mathematics' 曾因 RMM 缺表被判 ★2，必须回到 ★5/high。"""
        tiers, keys = prepare_tiers({'romanian master of mathematics': 5,
                                     'romanian masters of mathematics': 5})
        cn = m.norm_comp('Seventeenth Romanian Master of Mathematics 2025')
        assert m.grade(cn, None, tiers, mods(), DEFAULT, keys) == (5, 'high')
        # 表里没有 RMM 家族时的旧行为：直落 default ★2（这正是当时的错判）
        empty, empty_keys = prepare_tiers({'baltic way': 3})
        assert m.grade(cn, None, empty, mods(), DEFAULT, empty_keys) == (2, 'low')

    def test_b_english_ordinal_stripped_but_round_kept(self):
        """锚点①b：'tenth philippine…' 的届数要剥到精确命中（high）；'third round' 是轮次信息不得剥。"""
        tiers, keys = prepare_tiers({'philippine mathematical olympiad': 3})
        # 剥掉届数才能精确命中；未修复时最多靠家族回退拿到 mid
        assert m.grade('tenth philippine mathematical olympiad', None,
                       tiers, mods(), DEFAULT, keys) == (3, 'high')
        # 轮次词保留：整名进表，决赛轮的 base 4 不能退化成正赛的 3
        rounds, rkeys = prepare_tiers({'south african mathematical olympiad': 3,
                                       'third round south african mathematical olympiad': 4})
        assert m.grade('third round south african mathematical olympiad', None,
                       rounds, mods(), DEFAULT, rkeys) == (4, 'high')
        # 上一条只走精确命中，剥不剥序数都绿；真正吃 lookahead 的是**表键侧**：
        # 若把「first round …」剥成「round …」，_by_keyform 会把初轮键登记成正赛键的别名，
        # 正赛（★3）反而被扣成初轮档（★1）。
        pair, pkeys = prepare_tiers({'first round zeta olympiad': 1, 'round zeta olympiad': 3})
        assert pair['_by_keyform'] == {}
        assert m.grade('the round zeta olympiad', None, pair, mods(), DEFAULT, pkeys) == (3, 'high')

    def test_c_leading_the_matches_in_both_directions(self):
        """锚点①c：表键带 'the ' 而输入不带（或反之）都必须精确命中，不能掉到 default/家族回退。"""
        # 表里有 the，输入没有 → 走 _by_keyform 反查
        tiers, keys = prepare_tiers({'the problems of ukrainian authors': 4})
        assert m.grade('problems of ukrainian authors', None, tiers, mods(), DEFAULT, keys) == (4, 'high')
        # 表里没有 the，输入有 → 走 comp_variants 的去 the 写法
        tiers2, keys2 = prepare_tiers({'danube mathematical competition': 3})
        assert m.grade('the danube mathematical competition', None,
                       tiers2, mods(), DEFAULT, keys2) == (3, 'high')


class TestRealTierTableAnchors:
    """只读加载仓库真表（不联网、不写文件），守住锚点①的表内容本身。"""

    @staticmethod
    def _real():
        tr = m.load_yaml(m.TIER_PATH)
        tiers = {k: dict(v) for k, v in (tr.get('tiers') or {}).items()}
        keys = sorted([k for k in tiers if not k.startswith('_')], key=len, reverse=True)
        tiers['_by_keyform'] = {m.key_form(k): k for k in keys if m.key_form(k) != k}
        return tiers, keys, m.compile_modifiers(tr.get('fallback_modifiers') or []), tr['default_unknown']

    def test_rmm_family_present_at_star5(self):
        """锚点①a（表内容）：RMM 家族被整条删掉会让顶级邀请赛重新掉到 default ★2。"""
        tiers, _keys, _ms, _d = self._real()
        rmm = {k: v['base'] for k, v in tiers.items() if k != '_by_keyform' and 'romanian master' in k}
        assert rmm == {'romanian master of mathematics': 5, 'romanian masters of mathematics': 5}

    @pytest.mark.parametrize('raw,expect', [
        ('Seventeenth Romanian Master of Mathematics 2025', (5, 'high')),   # ①a+①b
        ('17th Romanian Masters of Mathematics', (5, 'high')),
        ('Tenth Philippine Mathematical Olympiad', (3, 'high')),            # ①b
        ('Problems of Ukrainian authors', (3, 'high')),                     # ①c
        ('The South African Mathematical Olympiad Third Round', (3, 'high')),
    ])
    def test_end_to_end_on_real_table(self, raw, expect):
        """锚点①端到端：真表 + 真修正器下，这五个曾错判的写法必须落到给定档位与置信度。"""
        tiers, keys, ms, default = self._real()
        assert m.grade(m.norm_comp(raw), None, tiers, ms, default, keys) == expect

    def test_real_modifiers_all_compile(self):
        """防回归：真表的 fallback_modifiers 语法必须全部可解析（新增条目写错 effect 会当场炸）。"""
        tr = m.load_yaml(m.TIER_PATH)
        kinds = {k for _rx, k, _v, _lc in m.compile_modifiers(tr['fallback_modifiers'])}
        assert kinds == {'set', 'delta', 'noop'}


# ---------------- 7. make_path_normalizer ----------------

class TestMakePathNormalizer:
    def test_rules_apply_in_chain(self):
        """防回归：normalize 段是链式的——后一条要能作用在前一条的产物上（表里第 5→6 条就是这样）。"""
        norm = m.make_path_normalizer([
            {'from': 'Geometry > Triangles > Centers: a', 'to': 'Geometry > Triangles > Centers: b'},
            {'from': 'Geometry > Triangles > Centers: b', 'to': 'Geometry > Triangles > Centers: c'},
        ])
        assert norm('Geometry > Triangles > Centers: a') == 'Geometry > Triangles > Centers: c'

    def test_generic_norm_applied_to_input_and_rules(self):
        """防回归：弯引号/多空格/'>' 两侧空白必须先归一，否则数据集里的变体写法查不到表。"""
        norm = m.make_path_normalizer([
            {'from': "Algebra > Polynomials > Gauss's Lemma", 'to': 'Algebra > Polynomials > Gauss lemma'},
        ])
        assert norm('Algebra  >Polynomials >  Gauss’s Lemma') == 'Algebra > Polynomials > Gauss lemma'

    def test_unmatched_path_passes_through_generic_norm_only(self):
        """防回归：未列入 normalize 的路径只做通用归一，不得被规则误改。"""
        norm = m.make_path_normalizer([{'from': 'A > B', 'to': 'A > C'}])
        assert norm('  Number Theory>Congruences  ') == 'Number Theory > Congruences'

    def test_empty_rule_list_is_identity_after_generic_norm(self):
        """防回归：空 normalize 段不得抛异常（表里删光规则时管线仍要能跑）。"""
        assert m.make_path_normalizer([])('A>B') == 'A > B'


# ---------------- 8. selfcheck（表自洽校验，不读数据集） ----------------

@pytest.fixture
def fake_root(tmp_path, monkeypatch):
    """把模块级 ROOT 指到临时目录：selfcheck 读的是合成 registry.yml，绝不碰仓库真表。"""
    tax = tmp_path / 'taxonomy'
    tax.mkdir()
    (tax / 'registry.yml').write_text(
        '_meta: 下划线开头是表注释段，不是板块\n'
        'algebra:\n'
        '  不等式:\n'
        '    - AM-GM\n'
        '  函数方程:\n'
        'geometry:\n'
        '  三角形与四边形基本性质:\n'
        '    - 导角\n',
        encoding='utf-8')
    monkeypatch.setattr(m, 'ROOT', str(tmp_path))
    return tmp_path


def _mp(map_=None, board=None, ignore=None):
    return {'map': map_ or {}, 'board_only': board or {}, 'ignore': ignore or {}}


def _tr(tiers=None, modifiers=None):
    return {'tiers': tiers or {'imo': {'base': 4}}, 'fallback_modifiers': modifiers or []}


class TestSelfcheck:
    def test_clean_tables_report_no_errors(self, fake_root):
        """防回归：合法表必须零报错，否则 --selfcheck 会天天误报（CI 门形同虚设）。"""
        mp = _mp(map_={'Algebra > Ineq': {'category': 'algebra', 'node': '不等式'}},
                 board={'Algebra > Other': {'category': 'algebra'}},
                 ignore={'Analysis > Measure': {'reason': '超奥数口径'}})
        assert m.selfcheck(mp, _tr()) == []

    def test_path_in_two_sections_is_reported(self, fake_root):
        """防回归：同一路径同时出现在 map/board_only/ignore 会让归档结果取决于判断顺序，必须报错。"""
        path = 'Algebra > Ineq'
        errs = m.selfcheck(_mp(map_={path: {'category': 'algebra', 'node': '不等式'}},
                               ignore={path: {'reason': '重复登记'}}), _tr())
        assert len(errs) == 1 and errs[0].startswith('路径同时出现在多段') and path in errs[0]

    def test_unknown_category_and_node_are_reported(self, fake_root):
        """防回归：map 指向 registry 里不存在的板块/节点时必须逐条点名（否则入库后 topics 解析不到）。"""
        errs = m.selfcheck(_mp(map_={
            'X > Bad cat': {'category': 'topology', 'node': '不等式'},
            'X > Bad node': {'category': 'algebra', 'node': '射影几何'},
        }), _tr())
        assert errs == ['map 非法板块 X > Bad cat: topology',
                        'map 非法节点 X > Bad node: algebra/射影几何']

    def test_underscore_key_is_not_a_valid_category(self, fake_root):
        """防回归：registry 里 '_' 开头的注释段不是板块，映射到它必须报非法板块。"""
        errs = m.selfcheck(_mp(map_={'X > Meta': {'category': '_meta', 'node': '不等式'}}), _tr())
        assert errs == ['map 非法板块 X > Meta: _meta']

    def test_board_only_category_checked(self, fake_root):
        """防回归：board_only 段只定板块，但板块名同样要在 registry 里。"""
        errs = m.selfcheck(_mp(board={'X > Other': {'category': 'topology'}}), _tr())
        assert errs == ['board_only 非法板块 X > Other']

    @pytest.mark.parametrize('base', [0, 6, '3', 3.0, None, True])
    def test_out_of_range_tier_base_is_reported(self, fake_root, base):
        """防回归：tier 的 base 必须是 1–5 的整数，越界/写成字符串会让 difficulty_est 落到 SPEC 之外。
        True 一项防 bool 是 int 子类——表里写 base: yes 会被当成 ★1 混过校验。"""
        errs = m.selfcheck(_mp(), _tr(tiers={'weird cup': {'base': base}}))
        assert errs == ['tier 非法 base: weird cup']

    @pytest.mark.parametrize('base', [1, 5])
    def test_boundary_tier_base_accepted(self, fake_root, base):
        """防回归：1 与 5 是合法边界，不能被越界校验误杀。"""
        assert m.selfcheck(_mp(), _tr(tiers={'edge cup': {'base': base}})) == []

    def test_broken_modifier_effect_raises(self, fake_root):
        """防回归：selfcheck 必须把修正器语法一并验掉，坏 effect 要在读数据集之前就炸出来。"""
        with pytest.raises(ValueError, match='无法解析 modifier effect'):
            m.selfcheck(_mp(), _tr(modifiers=[{'pattern': 'x', 'effect': '定为 2'}]))

    @pytest.mark.parametrize('search_in', ['problem', 'solutions', 'both'])
    def test_keyword_rule_search_in_valid_values_accepted(self, fake_root, search_in):
        """search_in 的三个合法值必须在不读取 MathNet 数据集的 selfcheck 中通过。"""
        mp = _mp()
        mp['keyword_rules'] = [
            {'pattern': 'TOKEN', 'category': 'algebra', 'node': '不等式', 'search_in': search_in},
        ]
        assert m.selfcheck(mp, _tr()) == []

    @pytest.mark.parametrize('search_in', ['statement', '', None, 1])
    def test_keyword_rule_invalid_search_in_reported(self, fake_root, search_in):
        """拼错、空值和错误类型必须在 load_dataset 前被表自洽校验拦下。"""
        mp = _mp()
        mp['keyword_rules'] = [
            {'pattern': 'TOKEN', 'category': 'algebra', 'node': '不等式', 'search_in': search_in},
        ]
        assert m.selfcheck(mp, _tr()) == [f"keyword_rules[1] 非法 search_in: {search_in!r}"]


# ---------------- 9. keyword_rules 匹配侧（合成数据，不读 HF） ----------------

KEYWORD_PATTERN = r'(?i)choose the (?:largest|smallest)'


def _keyword_rule(search_in_marker='missing'):
    rule = {'pattern': KEYWORD_PATTERN, 'category': 'combinatorics', 'node': '极端原理'}
    if search_in_marker != 'missing':
        rule['search_in'] = search_in_marker
    return m.compile_keyword_rules([rule])[0]


class TestKeywordRuleSearchIn:
    @pytest.mark.parametrize('search_in,problem,solutions,expect', [
        ('problem', 'Choose the largest vertex.', ['No matching technique here.'], True),
        ('problem', 'A plain graph problem.', ['Choose the smallest vertex.'], False),
        ('solutions', 'Choose the largest vertex.', ['No matching technique here.'], False),
        ('solutions', 'A plain graph problem.', ['Choose the smallest vertex.'], True),
        ('both', 'Choose the largest vertex.', ['No matching technique here.'], True),
        ('both', 'A plain graph problem.', ['Choose the smallest vertex.'], True),
    ])
    def test_three_search_sides_on_synthetic_rows(self, search_in, problem, solutions, expect):
        """三种 search_in 在合成行上分别只搜索约定侧，不加载 datasets 包或 HF 数据。"""
        row = {'problem_markdown': problem, 'solutions_markdown': solutions}
        assert bool(m.keyword_rule_matches(_keyword_rule(search_in), row)) is expect

    @pytest.mark.parametrize('solutions', [None, [], ['', None], 'Choose the smallest vertex.'])
    def test_solution_side_handles_empty_list_and_string(self, solutions):
        """MathNet 解答的空值/列表是正常输入；额外容忍单字符串，均不得抛异常。"""
        row = {'problem_markdown': '', 'solutions_markdown': solutions}
        expect = solutions == 'Choose the smallest vertex.'
        assert bool(m.keyword_rule_matches(_keyword_rule('solutions'), row)) is expect

    def test_missing_search_in_is_byte_identical_to_old_problem_expression(self):
        """缺省规则必须逐字节复现旧版 `rx.search(problem or '')` 的命中向量。"""
        rows = [
            {'problem_markdown': 'Choose the largest vertex.',
             'solutions_markdown': ['No matching technique here.']},
            {'problem_markdown': 'A plain graph problem.',
             'solutions_markdown': ['Choose the smallest vertex.']},
            {'problem_markdown': None, 'solutions_markdown': ['Choose the largest vertex.']},
        ]
        old = [bool(re.search(KEYWORD_PATTERN, row['problem_markdown'] or '')) for row in rows]
        new = [bool(m.keyword_rule_matches(_keyword_rule(), row)) for row in rows]
        old_bytes = json.dumps(old, separators=(',', ':')).encode()
        new_bytes = json.dumps(new, separators=(',', ':')).encode()
        assert new_bytes == old_bytes == b'[true,false,false]'


class TestRealKeywordRuleSides:
    def test_reviewed_rules_use_the_intended_search_side(self):
        """真表锁定本工单逐条侧别核查：技法双侧/解法侧显式标注，题面对象规则保持缺省。"""
        mp = m.load_yaml(m.MAP_PATH)
        sides = {rule['node']: rule.get('search_in', 'problem')
                 for rule in mp['keyword_rules']}
        assert sides['极端原理'] == 'solutions'
        assert sides['Ramsey 型问题'] == 'both'
        assert sides['p-adic 赋值与 LTE'] == 'both'
        assert sides['勾股定理与直角三角形'] == 'problem'
        assert sides['数字与进位制'] == 'problem'
