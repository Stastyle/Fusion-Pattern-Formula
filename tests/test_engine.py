"""Tests for FormulaPattern/engine.py (pure Python, no Fusion needed).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'FormulaPattern'))

import engine  # noqa: E402
from engine import (  # noqa: E402
    Formula, FormulaError, PatternSpec, PatternResult, compute_positions,
    MODE_POSITION, MODE_SPACING, SIZE_SCALE, SIZE_DIAMETER, PRESETS, presets_for, preset_by_id,
    format_number, preview_html, polar_to_xy,
)


class FormulaEvaluation(unittest.TestCase):
    def test_plain_arithmetic(self):
        self.assertAlmostEqual(Formula('1 + 2 * 3').evaluate({}), 7.0)

    def test_caret_means_power(self):
        self.assertAlmostEqual(Formula('2 ^ 10').evaluate({}), 1024.0)

    def test_variables_are_substituted(self):
        self.assertAlmostEqual(Formula('a * i + b').evaluate({'a': 2, 'i': 3, 'b': 1}), 7.0)

    def test_functions_and_constants(self):
        self.assertAlmostEqual(Formula('sqrt(16) + cos(pi) + abs(-2)').evaluate({}), 5.0)
        self.assertAlmostEqual(Formula('sind(90) + cosd(0)').evaluate({}), 2.0)
        self.assertAlmostEqual(Formula('phi').evaluate({}), (1 + 5 ** 0.5) / 2)
        self.assertAlmostEqual(Formula('log(8, 2) + ln(e) + log10(100)').evaluate({}), 6.0)
        self.assertAlmostEqual(Formula('fib(10)').evaluate({}), 55.0)
        self.assertAlmostEqual(Formula('clamp(15, 0, 10) + sign(-3)').evaluate({}), 9.0)
        self.assertAlmostEqual(Formula('max(1, 2, 3) + min(4, 5)').evaluate({}), 7.0)

    def test_conditional_expression(self):
        f = Formula('10 if i < 3 else 20')
        self.assertAlmostEqual(f.evaluate({'i': 1}), 10.0)
        self.assertAlmostEqual(f.evaluate({'i': 5}), 20.0)

    def test_result_is_always_a_float(self):
        self.assertIsInstance(Formula('2 ^ 3').evaluate({}), float)
        self.assertIsInstance(Formula('i > 1').evaluate({'i': 2}), float)

    def test_names_lists_variables_but_not_functions_or_constants(self):
        self.assertEqual(Formula('a * sin(i * pi) + L').names, {'a', 'i', 'L'})


class FormulaRejection(unittest.TestCase):
    def test_empty_formula(self):
        with self.assertRaises(FormulaError):
            Formula('   ')

    def test_syntax_error_is_reported_plainly(self):
        with self.assertRaises(FormulaError) as cm:
            Formula('2 * (i + ')
        self.assertIn('read', str(cm.exception).lower())

    def test_unknown_name_is_named_in_message(self):
        with self.assertRaises(FormulaError) as cm:
            Formula('a * L').evaluate({'a': 1})
        self.assertIn("'L'", str(cm.exception))

    def test_attribute_access_is_rejected(self):
        with self.assertRaises(FormulaError):
            Formula('().__class__')

    def test_dunder_and_builtins_are_rejected(self):
        with self.assertRaises(FormulaError):
            Formula('__import__("os")')
        with self.assertRaises(FormulaError):
            Formula('open("x")')

    def test_subscript_lambda_and_strings_are_rejected(self):
        for bad in ('a[0]', '(lambda: 1)()', '"abc"', 'a := 1', '[1,2]'):
            with self.assertRaises(FormulaError, msg=bad):
                Formula(bad).evaluate({'a': 1})

    def test_division_by_zero(self):
        with self.assertRaises(FormulaError) as cm:
            Formula('1 / (i - 1)').evaluate({'i': 1})
        self.assertIn('zero', str(cm.exception).lower())

    def test_runaway_power_does_not_hang(self):
        with self.assertRaises(FormulaError):
            Formula('10 ^ 10 ^ 10').evaluate({})

    def test_complex_result_is_rejected(self):
        with self.assertRaises(FormulaError):
            Formula('(-8) ^ (1/3)').evaluate({})

    def test_math_domain_error(self):
        with self.assertRaises(FormulaError):
            Formula('sqrt(-1)').evaluate({})

    def test_function_arity_error_is_friendly(self):
        with self.assertRaises(FormulaError) as cm:
            Formula('sqrt(1, 2)').evaluate({})
        self.assertIn('sqrt', str(cm.exception))


def spec(**kw):
    base = dict(formula='a * i', count=4, mode=MODE_POSITION, circular=False,
                flip=False, a=10.0, b=0.0, c=0.0, extra_vars=None)
    base.update(kw)
    return PatternSpec(**base)


class Positions(unittest.TestCase):
    def test_position_mode_uses_formula_directly(self):
        r = compute_positions(spec(formula='a * i', count=4, a=10))
        self.assertEqual([round(p, 6) for p in r.positions], [10, 20, 30, 40])
        self.assertEqual([round(p, 6) for p in r.values], [10, 20, 30, 40])

    def test_spacing_mode_accumulates_gaps(self):
        r = compute_positions(spec(formula='a * b^(i-1)', count=4, mode=MODE_SPACING, a=10, b=2))
        self.assertEqual([round(p, 6) for p in r.values], [10, 20, 40, 80])
        self.assertEqual([round(p, 6) for p in r.positions], [10, 30, 70, 150])

    def test_variables_i_n_t_are_available(self):
        r = compute_positions(spec(formula='i + n + t', count=2))
        self.assertAlmostEqual(r.positions[0], 1 + 2 + 0.5)
        self.assertAlmostEqual(r.positions[1], 2 + 2 + 1.0)

    def test_fret_formula_matches_known_fret_positions(self):
        # 648 mm scale, 12-TET: fret 12 is exactly half the scale.
        r = compute_positions(spec(formula='a * (1 - 1 / 2^(i/12))', count=12, a=648))
        self.assertAlmostEqual(r.positions[0], 648 * (1 - 2 ** (-1 / 12)), places=6)
        self.assertAlmostEqual(r.positions[0], 36.369, places=3)
        self.assertAlmostEqual(r.positions[11], 324.0, places=6)

    def test_flip_negates_positions(self):
        r = compute_positions(spec(formula='a * i', count=2, a=5, flip=True))
        self.assertEqual([round(p, 6) for p in r.positions], [-5, -10])

    def test_extra_vars_are_usable_as_user_parameters(self):
        r = compute_positions(spec(formula='scale_length * i', count=1, extra_vars={'scale_length': 25.5}))
        self.assertAlmostEqual(r.positions[0], 25.5)

    def test_builtin_variables_win_over_extra_vars(self):
        r = compute_positions(spec(formula='i', count=1, extra_vars={'i': 999}))
        self.assertAlmostEqual(r.positions[0], 1.0)

    def test_copy_on_the_original_is_an_error_naming_the_copy(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * sin(pi * t)', count=4, a=10))
        self.assertIn('4', str(cm.exception))
        self.assertIn('original', str(cm.exception).lower())

    def test_formula_error_names_the_copy(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 3)', count=4))
        self.assertIn('i = 3', str(cm.exception))

    def test_duplicates_produce_a_warning_not_an_error(self):
        r = compute_positions(spec(formula='10 if i < 3 else 10 * (i - 1)', count=3))
        self.assertEqual([round(p, 6) for p in r.positions], [10, 10, 20])
        self.assertTrue(any('same' in w.lower() for w in r.warnings))

    def test_count_must_be_positive(self):
        with self.assertRaises(FormulaError):
            compute_positions(spec(count=0))

    def test_extent_is_reported(self):
        r = compute_positions(spec(formula='a * i', count=3, a=10))
        self.assertAlmostEqual(r.extent, 30.0)


class CircularPositions(unittest.TestCase):
    def test_angles_are_reduced_modulo_360_keeping_sign(self):
        r = compute_positions(spec(formula='a * i', count=3, a=137.5, circular=True))
        self.assertEqual([round(p, 6) for p in r.positions], [137.5, 275.0, 52.5])
        r = compute_positions(spec(formula='-a * i', count=3, a=137.5, circular=True))
        self.assertEqual([round(p, 6) for p in r.positions], [-137.5, -275.0, -52.5])

    def test_multiple_of_360_is_an_error(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=4, a=90, circular=True))
        self.assertIn('4', str(cm.exception))

    def test_spacing_mode_accumulates_angles_before_reduction(self):
        r = compute_positions(spec(formula='a', count=5, a=100, mode=MODE_SPACING, circular=True))
        self.assertEqual([round(p, 6) for p in r.positions], [100, 200, 300, 40, 140])


class Presets(unittest.TestCase):
    def test_every_preset_evaluates_cleanly_for_linear_and_circular(self):
        for p in PRESETS:
            for circular in (False, True):
                if circular and 'circular' not in p.kinds:
                    continue
                if not circular and 'linear' not in p.kinds:
                    continue
                s = spec(formula=p.formula, count=12, mode=p.mode, circular=circular,
                         a=(p.a_deg if circular else p.a_mm), b=p.b, c=p.c)
                r = compute_positions(s)
                self.assertEqual(len(r.positions), 12, p.id)
                self.assertTrue(all(math.isfinite(x) for x in r.positions), p.id)

    def test_linear_presets_are_strictly_increasing(self):
        for p in PRESETS:
            if 'linear' not in p.kinds:
                continue
            r = compute_positions(spec(formula=p.formula, count=12, mode=p.mode, a=p.a_mm, b=p.b, c=p.c))
            self.assertEqual(r.positions, sorted(r.positions), p.id)
            self.assertEqual(len(set(round(x, 9) for x in r.positions)), 12, p.id)

    def test_presets_for_filters_by_pattern_kind(self):
        linear_ids = [p.id for p in presets_for(circular=False)]
        circular_ids = [p.id for p in presets_for(circular=True)]
        self.assertIn('frets', linear_ids)
        self.assertNotIn('frets', circular_ids)
        self.assertIn('constant', circular_ids)
        self.assertEqual(linear_ids[0], 'custom')

    def test_preset_lookup(self):
        self.assertEqual(preset_by_id('frets').mode, MODE_POSITION)
        self.assertIsNone(preset_by_id('nope'))

    def test_preset_notes_mention_each_tunable_they_use(self):
        for p in PRESETS:
            if p.id == 'custom':
                continue
            used = Formula(p.formula).names
            if p.formula2:
                used |= Formula(p.formula2).names
            for name in ('a', 'b', 'c'):
                if name in used:
                    self.assertIn(name + ' ', p.notes, f'{p.id} notes should explain {name}')

    def test_presets_for_path_lists_the_path_presets_in_order(self):
        self.assertEqual([p.id for p in presets_for('path')],
                         ['custom', 'spiral', 'log_spiral', 'phyllotaxis', 'sine', 'ellipse', 'grid'])

    def test_linear_and_circular_preset_lists_are_unchanged(self):
        self.assertEqual([p.id for p in presets_for('linear')],
                         ['custom', 'constant', 'frets', 'geometric', 'arithmetic',
                          'ease_sine', 'ease_power', 'fibonacci'])
        self.assertEqual([p.id for p in presets_for('circular')],
                         ['custom', 'constant', 'geometric', 'arithmetic',
                          'ease_sine', 'ease_power', 'fibonacci', 'golden_angle'])

    def test_presets_for_accepts_a_bool_or_a_kind(self):
        self.assertEqual(presets_for(True), presets_for('circular'))
        self.assertEqual(presets_for(False), presets_for('linear'))
        self.assertEqual(presets_for(circular=True), presets_for('circular'))
        self.assertEqual(presets_for(circular=False), presets_for('linear'))

    def test_only_custom_is_shared_between_path_and_the_other_kinds(self):
        path_ids = {p.id for p in presets_for('path')}
        other_ids = {p.id for p in presets_for('linear')} | {p.id for p in presets_for('circular')}
        self.assertEqual(path_ids & other_ids, {'custom'})

    def test_path_presets_carry_a_second_formula_and_coordinates(self):
        for p in presets_for('path'):
            self.assertTrue(p.formula2.strip(), p.id)
            self.assertIsInstance(p.polar, bool, p.id)
        for p in PRESETS:
            if 'path' not in p.kinds:
                self.assertEqual(p.formula2, '', p.id)
                self.assertFalse(p.polar, p.id)
        self.assertTrue(preset_by_id('spiral').polar)
        self.assertFalse(preset_by_id('sine').polar)
        self.assertEqual(preset_by_id('custom').formula2, '0')

    def test_every_path_preset_evaluates_cleanly_without_overlaps(self):
        for p in presets_for('path'):
            for count in (1, 5, 12, 30):
                s = spec(formula=p.formula, formula2=p.formula2, path=True, polar=p.polar,
                         count=count, mode=p.mode, a=p.a_mm, b=p.b, c=p.c)
                r = compute_positions(s)
                self.assertEqual(len(r.points), count, p.id)
                self.assertTrue(all(math.isfinite(x) and math.isfinite(y) for x, y in r.points), p.id)
                self.assertEqual(r.warnings, [], f'{p.id} with {count} copies')

    def test_grid_preset_fills_rows_leaving_the_original_in_the_corner(self):
        p = preset_by_id('grid')
        r = compute_positions(spec(formula=p.formula, formula2=p.formula2, path=True, polar=p.polar,
                                   count=6, a=p.a_mm, b=p.b, c=p.c))
        rounded = [(round(x, 6), round(y, 6)) for x, y in r.points]
        self.assertEqual(rounded, [(10, 0), (20, 0), (30, 0), (40, 0), (0, 10), (10, 10)])


class Formatting(unittest.TestCase):
    def test_format_number_trims_trailing_zeros(self):
        self.assertEqual(format_number(324.0), '324')
        self.assertEqual(format_number(36.37181), '36.372')
        self.assertEqual(format_number(-0.5), '-0.5')
        self.assertEqual(format_number(0.0004), '0')

    def test_preview_html_lists_positions_with_units(self):
        r = compute_positions(spec(formula='a * i', count=3, a=10))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION)
        self.assertIn('3 copies', html)
        self.assertIn('30 mm', html)
        self.assertIn('i = 2', html)

    def test_preview_html_shows_gap_and_position_in_spacing_mode(self):
        r = compute_positions(spec(formula='a', count=3, a=10, mode=MODE_SPACING))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_SPACING)
        self.assertIn('+10', html)
        self.assertIn('30 mm', html)

    def test_preview_html_elides_long_lists_but_keeps_last(self):
        r = compute_positions(spec(formula='a * i', count=40, a=1))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION)
        self.assertIn('i = 40', html)
        self.assertNotIn('i = 20:', html)
        self.assertIn('…', html)

    def test_preview_html_uses_degrees_for_circular(self):
        r = compute_positions(spec(formula='a * i', count=2, a=30, circular=True))
        html = preview_html(r, unit='mm', circular=True, mode=MODE_POSITION)
        self.assertIn('60°', html)
        self.assertNotIn('mm', html)

    def test_preview_html_includes_warnings(self):
        r = compute_positions(spec(formula='10 if i < 3 else 10 * (i - 1)', count=3))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION)
        self.assertIn('same', html.lower())

    def test_reference_html_mentions_variables_and_power(self):
        self.assertIn('<b>t</b>', engine.REFERENCE_HTML)
        self.assertIn('^', engine.REFERENCE_HTML)

    def test_reference_html_explains_path_and_vary_each_copy(self):
        ref = engine.REFERENCE_HTML
        self.assertIn('<b>Vary each copy</b>', ref)
        self.assertIn('<b>Path</b> ', ref)
        for example in ('b * i', '1 + 0.1 * i', 'a + 0.1 * i', 'a * ((i * i) % 7) / 7'):
            self.assertIn(example, ref)
        self.assertIn('degrees', ref)


LINEAR_PREVIEW = '<b>3 copies</b> · farthest copy 30 mm<br>i = 1: 10 mm<br>i = 2: 20 mm<br>i = 3: 30 mm'


class PreviewOfPathAndVary(unittest.TestCase):
    def test_linear_preview_is_unchanged_when_nothing_varies(self):
        r = compute_positions(spec(formula='a * i', count=3, a=10, rotation='', size='', depth=''))
        self.assertEqual(preview_html(r, unit='mm', circular=False, mode=MODE_POSITION), LINEAR_PREVIEW)
        # A result built the old way (without the new fields) previews the same.
        old = PatternResult(values=r.values, positions=r.positions, extent=r.extent)
        self.assertEqual(preview_html(old, 'mm', False, MODE_POSITION, False, 12), LINEAR_PREVIEW)

    def test_path_lines_show_points_and_the_farthest_copy(self):
        r = compute_positions(spec(formula='a * i', formula2='b * i', path=True, count=2, a=3, b=4))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION, path=True)
        self.assertEqual(html, '<b>2 copies</b> · farthest copy 10 mm<br>i = 1: (3, 4) mm<br>i = 2: (6, 8) mm')

    def test_path_preview_ignores_mode_and_circular(self):
        r = compute_positions(spec(formula='a * i', formula2='b * i', path=True, count=1, a=3, b=4))
        html = preview_html(r, unit='in', circular=True, mode=MODE_SPACING, path=True)
        self.assertEqual(html, '<b>1 copy</b> · farthest copy 5 in<br>i = 1: (3, 4) in')

    def test_path_preview_still_elides_and_warns(self):
        r = compute_positions(spec(formula='10', formula2='10 if i < 3 else 10 * i', path=True, count=40))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION, path=True)
        self.assertIn('i = 40: (10, 400) mm', html)
        self.assertNotIn('i = 20:', html)
        self.assertIn('…', html)
        self.assertIn('<i>⚠ Copies 1 and 2 land on the same spot</i>', html)

    def test_vary_suffixes_follow_each_line_in_order(self):
        r = compute_positions(spec(formula='a * i', count=2, a=10, rotation='30 * i', size='1 + 0.1 * i', depth='a'))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION)
        self.assertEqual(html, '<b>2 copies</b> · farthest copy 20 mm<br>'
                               'i = 1: 10 mm · turn 30° · ×1.1 · depth 10 mm<br>'
                               'i = 2: 20 mm · turn 60° · ×1.2 · depth 10 mm')

    def test_diameter_size_shows_the_unit(self):
        r = compute_positions(spec(formula='a * i', count=1, a=10, size='8'))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION, size_kind=SIZE_DIAMETER)
        self.assertTrue(html.endswith('i = 1: 10 mm · ⌀8 mm'), html)
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION, size_kind=SIZE_SCALE)
        self.assertTrue(html.endswith('i = 1: 10 mm · ×8'), html)

    def test_each_vary_field_appears_on_its_own(self):
        r = compute_positions(spec(formula='a * i', count=1, a=10, rotation='45'))
        self.assertTrue(preview_html(r, 'mm', False, MODE_POSITION).endswith('i = 1: 10 mm · turn 45°'))
        r = compute_positions(spec(formula='a * i', count=1, a=10, depth='-2.5'))
        self.assertTrue(preview_html(r, 'mm', False, MODE_POSITION).endswith('i = 1: 10 mm · depth -2.5 mm'))

    def test_vary_suffix_comes_after_the_spacing_gap(self):
        r = compute_positions(spec(formula='a', count=2, a=10, mode=MODE_SPACING, rotation='b * i', b=5))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_SPACING)
        self.assertIn('i = 2: 20 mm (+10) · turn 10°', html)

    def test_circular_preview_keeps_degrees_but_depth_uses_the_length_unit(self):
        r = compute_positions(spec(formula='a * i', count=1, a=30, circular=True, depth='8'))
        html = preview_html(r, unit='mm', circular=True, mode=MODE_POSITION)
        self.assertEqual(html, '<b>1 copy</b> · largest angle 30°<br>i = 1: 30° · depth 8 mm')

    def test_path_lines_take_vary_suffixes_too(self):
        r = compute_positions(spec(formula='a * i', formula2='0', path=True, count=1, a=10, rotation='90'))
        html = preview_html(r, unit='mm', circular=False, mode=MODE_POSITION, path=True)
        self.assertTrue(html.endswith('i = 1: (10, 0) mm · turn 90°'), html)


class PathPositions(unittest.TestCase):
    def test_spec_and_result_defaults_keep_old_callers_working(self):
        s = PatternSpec('2 * i', 3)
        self.assertEqual((s.path, s.formula2, s.polar, s.rotation, s.size, s.size_kind, s.depth),
                         (False, '', False, '', '', SIZE_SCALE, ''))
        self.assertEqual((SIZE_SCALE, SIZE_DIAMETER), ('scale', 'diameter'))
        r = compute_positions(s)
        self.assertEqual((r.values2, r.points, r.rotations, r.sizes, r.depths), ([], [], [], [], []))

    def test_polar_to_xy(self):
        x, y = polar_to_xy(10, 90)
        self.assertAlmostEqual(x, 0.0)
        self.assertAlmostEqual(y, 10.0)
        x, y = polar_to_xy(2, -180)
        self.assertAlmostEqual(x, -2.0)
        self.assertAlmostEqual(y, 0.0)
        self.assertEqual(polar_to_xy(3, 0), (3.0, 0.0))

    def test_cartesian_points_distances_and_raw_values(self):
        r = compute_positions(spec(formula='a * i', formula2='b * i', path=True, count=2, a=3, b=4))
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y in r.points], [(3, 4), (6, 8)])
        self.assertEqual([round(p, 6) for p in r.positions], [5, 10])
        self.assertAlmostEqual(r.extent, 10.0)
        self.assertEqual(r.values, [3.0, 6.0])
        self.assertEqual(r.values2, [4.0, 8.0])
        self.assertEqual(r.count, 2)
        self.assertEqual(r.warnings, [])

    def test_polar_points_use_degrees(self):
        r = compute_positions(spec(formula='a', formula2='90 * i', path=True, polar=True, count=3, a=10))
        expected = [(0, 10), (-10, 0), (0, -10)]
        for (x, y), (ex, ey) in zip(r.points, expected):
            self.assertAlmostEqual(x, ex)
            self.assertAlmostEqual(y, ey)
        self.assertEqual([round(p, 6) for p in r.positions], [10, 10, 10])
        self.assertEqual(r.values, [10.0, 10.0, 10.0])
        self.assertEqual(r.values2, [90.0, 180.0, 270.0])

    def test_flip_negates_y_only(self):
        r = compute_positions(spec(formula='a * i', formula2='b * i', path=True, count=2, a=3, b=4, flip=True))
        self.assertEqual([(round(x, 6), round(y, 6)) for x, y in r.points], [(3, -4), (6, -8)])
        r = compute_positions(spec(formula='a', formula2='90', path=True, polar=True, count=1, a=10, flip=True))
        self.assertAlmostEqual(r.points[0][0], 0.0)
        self.assertAlmostEqual(r.points[0][1], -10.0)

    def test_mode_and_circular_are_ignored_for_a_path(self):
        base = spec(formula='a * i', formula2='b * i', path=True, count=3, a=3, b=4)
        spacing = spec(formula='a * i', formula2='b * i', path=True, count=3, a=3, b=4,
                       mode=MODE_SPACING, circular=True)
        self.assertEqual(compute_positions(spacing).points, compute_positions(base).points)
        self.assertEqual(compute_positions(spacing).positions, compute_positions(base).positions)

    def test_environment_has_i_n_t_and_user_parameters(self):
        r = compute_positions(spec(formula='i + n', formula2='t + L', path=True, count=2, extra_vars={'L': 100}))
        self.assertEqual(r.points, [(3.0, 100.5), (4.0, 101.0)])

    def test_point_on_the_original_is_an_error_naming_the_copy(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * (i - 1)', formula2='0', path=True, count=3, a=10))
        self.assertEqual(str(cm.exception), 'Copy 1 lands on the original (its offset is 0). '
                                            'Change the formula so every copy moves away from it')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1e-8', formula2='1e-8', path=True, count=1))
        self.assertIn('Copy 1 lands on the original', str(cm.exception))

    def test_duplicate_points_warn_but_equal_distances_do_not(self):
        r = compute_positions(spec(formula='10', formula2='10 if i < 3 else 20', path=True, count=3))
        self.assertEqual(r.warnings, ['Copies 1 and 2 land on the same spot'])
        # (3, 4) and (4, 3) are both 5 away from the original but are different points.
        r = compute_positions(spec(formula='3 if i == 1 else 4', formula2='4 if i == 1 else 3', path=True, count=2))
        self.assertEqual([round(p, 6) for p in r.positions], [5, 5])
        self.assertEqual(r.warnings, [])
        # Same x, different y is not a duplicate either.
        r = compute_positions(spec(formula='a', formula2='b * i', path=True, count=4, a=10, b=5))
        self.assertEqual(r.warnings, [])

    def test_many_duplicate_points_are_summarised(self):
        r = compute_positions(spec(formula='10', formula2='10', path=True, count=6))
        self.assertEqual(len(r.warnings), 4)
        self.assertEqual(r.warnings[0], 'Copies 1 and 2 land on the same spot')
        self.assertTrue(r.warnings[-1].startswith('… and '), r.warnings[-1])

    def test_formula_errors_say_which_formula_and_copy(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 3)', formula2='1', path=True, count=4))
        self.assertEqual(str(cm.exception), 'Copy i = 3, X formula: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1', formula2='1 / (i - 3)', path=True, count=4))
        self.assertEqual(str(cm.exception), 'Copy i = 3, Y formula: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 3)', formula2='1', path=True, polar=True, count=4))
        self.assertEqual(str(cm.exception), 'Copy i = 3, radius formula: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1', formula2='1 / (i - 3)', path=True, polar=True, count=4))
        self.assertEqual(str(cm.exception), 'Copy i = 3, angle formula: Division by zero')

    def test_unreadable_second_formula_names_itself(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1', formula2='2 * (i + ', path=True, count=2))
        self.assertTrue(str(cm.exception).startswith("Y formula: Can't read the formula"), str(cm.exception))
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='2 * (i + ', formula2='1', path=True, polar=True, count=2))
        self.assertTrue(str(cm.exception).startswith("Radius formula: Can't read the formula"), str(cm.exception))

    def test_empty_second_formula(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', formula2='  ', path=True, count=2))
        self.assertEqual(str(cm.exception), 'The Y formula is empty')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', formula2='', path=True, polar=True, count=2))
        self.assertEqual(str(cm.exception), 'The angle formula is empty')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='', formula2='1', path=True, count=2))
        self.assertEqual(str(cm.exception), 'The X formula is empty')

    def test_linear_and_circular_error_wording_is_unchanged(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 3)', count=4))
        self.assertEqual(str(cm.exception), 'Copy i = 3: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 3)', count=4, circular=True))
        self.assertEqual(str(cm.exception), 'Copy i = 3: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='', count=4))
        self.assertEqual(str(cm.exception), 'The formula is empty')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * (i - 1)', count=2))
        self.assertEqual(str(cm.exception), 'Copy 1 lands on the original (its distance is 0). '
                                            'Change the formula so every copy moves away from it')

    def test_second_formula_is_ignored_unless_path(self):
        r = compute_positions(spec(formula='a * i', formula2='1 / 0', count=2, a=10))
        self.assertEqual([round(p, 6) for p in r.positions], [10, 20])
        self.assertEqual(r.points, [])
        self.assertEqual(r.values2, [])


class VaryEachCopy(unittest.TestCase):
    def test_lists_are_empty_when_the_fields_are_blank(self):
        for kw in (dict(), dict(path=True, formula2='0'), dict(circular=True)):
            r = compute_positions(spec(formula='a * i', count=3, a=10, rotation='', size='  ', depth='', **kw))
            self.assertEqual((r.rotations, r.sizes, r.depths), ([], [], []), kw)

    def test_rotation_size_and_depth_are_evaluated_per_copy(self):
        r = compute_positions(spec(formula='a * i', count=3, a=70, b=30,
                                   rotation='b * i', size='1 + 0.1 * i', depth='a * ((i * i) % 7) / 7'))
        self.assertEqual([round(x, 6) for x in r.rotations], [30, 60, 90])
        self.assertEqual([round(x, 6) for x in r.sizes], [1.1, 1.2, 1.3])
        self.assertEqual([round(x, 6) for x in r.depths], [10, 40, 20])
        self.assertEqual(len(r.positions), 3)

    def test_qrd_depth_example_over_six_wells(self):
        r = compute_positions(spec(formula='a * i', count=6, a=70, depth='a * ((i * i) % 7) / 7'))
        self.assertEqual([round(d, 6) for d in r.depths], [10, 40, 20, 20, 40, 10])

    def test_vary_fields_apply_to_circular_and_path_patterns(self):
        r = compute_positions(spec(formula='a * i', count=2, a=30, circular=True, rotation='10 * i', size='2', depth='3'))
        self.assertEqual((r.rotations, r.sizes, r.depths), ([10.0, 20.0], [2.0, 2.0], [3.0, 3.0]))
        r = compute_positions(spec(formula='a * i', formula2='0', path=True, count=2, a=10, rotation='t * 90'))
        self.assertEqual(r.rotations, [45.0, 90.0])

    def test_vary_fields_see_the_same_environment(self):
        r = compute_positions(spec(formula='a * i', count=2, a=10, b=2, c=3,
                                   rotation='n + i', size='b + c * t', depth='L', extra_vars={'L': 7}))
        self.assertEqual(r.rotations, [3.0, 4.0])
        self.assertEqual(r.sizes, [3.5, 5.0])
        self.assertEqual(r.depths, [7.0, 7.0])

    def test_rotation_has_no_restriction(self):
        r = compute_positions(spec(formula='a * i', count=3, a=10, rotation='(i - 2) * 400'))
        self.assertEqual(r.rotations, [-400.0, 0.0, 400.0])

    def test_size_must_be_greater_than_zero(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=3, a=10, size='abs(i - 2)'))
        self.assertEqual(str(cm.exception), 'Copy 2: size must be greater than 0')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=3, a=10, size='-1'))
        self.assertEqual(str(cm.exception), 'Copy 1: size must be greater than 0')
        r = compute_positions(spec(formula='a * i', count=1, a=10, size='0.001'))
        self.assertEqual(r.sizes, [0.001])

    def test_depth_must_not_be_zero_but_may_be_negative(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=3, a=10, depth='i - 2'))
        self.assertEqual(str(cm.exception), 'Copy 2 has depth 0')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=1, a=10, depth='1e-9'))
        self.assertEqual(str(cm.exception), 'Copy 1 has depth 0')
        r = compute_positions(spec(formula='a * i', count=2, a=10, depth='-a'))
        self.assertEqual(r.depths, [-10.0, -10.0])

    def test_vary_errors_name_the_field_and_the_copy(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=4, a=10, rotation='1 / (i - 3)'))
        self.assertEqual(str(cm.exception), 'Rotation, copy i = 3: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=4, a=10, size='1 / (i - 3)'))
        self.assertEqual(str(cm.exception), 'Size, copy i = 3: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=4, a=10, depth='1 / (i - 3)'))
        self.assertEqual(str(cm.exception), 'Depth, copy i = 3: Division by zero')
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='a * i', count=4, a=10, rotation='b *'))
        self.assertTrue(str(cm.exception).startswith("Rotation: Can't read the formula"), str(cm.exception))

    def test_position_errors_come_before_vary_errors(self):
        with self.assertRaises(FormulaError) as cm:
            compute_positions(spec(formula='1 / (i - 2)', count=3, rotation='1 / 0'))
        self.assertEqual(str(cm.exception), 'Copy i = 2: Division by zero')


if __name__ == '__main__':
    unittest.main()
