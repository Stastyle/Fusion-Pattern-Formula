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
    Formula, FormulaError, PatternSpec, compute_positions,
    MODE_POSITION, MODE_SPACING, PRESETS, presets_for, preset_by_id,
    format_number, preview_html,
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
            for name in ('a', 'b', 'c'):
                if name in used:
                    self.assertIn(name + ' ', p.notes, f'{p.id} notes should explain {name}')


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


if __name__ == '__main__':
    unittest.main()
