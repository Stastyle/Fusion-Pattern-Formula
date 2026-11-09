"""Exercise FormulaPattern/command.py against the fake adsk package in tests/fake_adsk.

This cannot prove the add-in works inside Fusion, but it catches the usual
add-in killers: typos in input ids, wrong attribute names, broken event
wiring, wrong unit conversions and wrong pattern-feature parameters.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import json
import math
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fake_adsk'))
sys.path.insert(0, ROOT)

import adsk.core  # noqa: E402  (the fake)
import adsk.fusion  # noqa: E402
from FormulaPattern import command  # noqa: E402
from FormulaPattern import FormulaPattern as entry  # noqa: E402

C = command


class FusionHarness(unittest.TestCase):
    """Starts the add-in against a fresh fake application for every test."""

    def setUp(self):
        global command, C
        adsk.core.Application.reset()
        self.app = adsk.core.Application.get()
        self.ui = self.app.userInterface
        self.design = adsk.fusion.Design('mm')
        self.app.activeProduct = self.design
        self.saved = []
        entry.run(None)
        self.assertEqual(self.ui.messages, [], 'start() must not pop up errors')
        command = C = entry.command      # run() loads the modules fresh, so patch the live one
        patches = [
            mock.patch.object(command, 'load_settings', return_value={}),
            mock.patch.object(command, 'save_settings', side_effect=self.saved.append),
            mock.patch.object(command, 'LOG_FILE', None),     # no diagnostics file from the tests
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        entry.stop(None)

    # ---- helpers ----

    def create_dialog(self):
        """Fire commandCreated only: the state Fusion is in before the dialog is on screen."""
        cmd = adsk.core.Command()
        cmd_def = self.ui.commandDefinitions.itemById(C.CMD_ID)
        cmd_def.commandCreated.fire(adsk.core.CommandCreatedEventArgs(cmd))
        self.assertEqual(self.ui.messages, [], 'opening the dialog must not pop up errors')
        dialog = next(h.dialog for h in command._handlers if hasattr(h, 'dialog'))
        return cmd, dialog

    def open_dialog(self):
        """commandCreated followed by activation, as Fusion does when it shows the dialog."""
        cmd, dialog = self.create_dialog()
        cmd.show()
        self.assertEqual(self.ui.messages, [], 'showing the dialog must not pop up errors')
        return cmd, dialog

    def changed(self, cmd, input_id):
        inp = cmd.commandInputs.itemById(input_id)
        cmd.inputChanged.fire(adsk.core.InputChangedEventArgs(inp, cmd.commandInputs))
        self.assertEqual(self.ui.messages, [], 'input change must not pop up errors')

    def choose(self, cmd, input_id, name):
        dd = cmd.commandInputs.itemById(input_id)
        item = next(i for i in dd.listItems if i.name == name)
        item.isSelected = True
        self.changed(cmd, input_id)

    def set_text(self, cmd, input_id, text):
        cmd.commandInputs.itemById(input_id).value = text
        self.changed(cmd, input_id)

    def set_expression(self, cmd, input_id, text):
        cmd.commandInputs.itemById(input_id).expression = text
        self.changed(cmd, input_id)

    def select(self, cmd, input_id, *entities):
        """What a user does: click the input (it takes focus), then click the entities."""
        sel = cmd.commandInputs.itemById(input_id)
        sel.hasFocus = True
        for e in entities:
            added = sel.addSelection(e if isinstance(e, adsk.core.Base) else adsk.fusion.Entity(e))
            self.assertTrue(added, f'{input_id} refused {e}')
        self.changed(cmd, input_id)

    def validate(self, cmd):
        return cmd.validateInputs.fire(adsk.core.ValidateInputsEventArgs()).areInputsValid

    def preview(self, cmd):
        return cmd.executePreview.fire(adsk.core.CommandEventArgs(cmd))

    def preview_text(self, cmd):
        return cmd.commandInputs.itemById(C.ID_PREVIEW).text

    def ready_linear(self, cmd):
        self.select(cmd, C.ID_OBJECTS, 'slot')
        self.select(cmd, C.ID_DIRECTION, 'neck edge')


class Toolbar(FusionHarness):
    def test_button_is_registered_after_the_native_rectangular_pattern_and_promoted(self):
        control = self.ui.create_panel.controls.itemById(C.CMD_ID)
        self.assertIsNotNone(control)
        self.assertEqual(control.positionID, 'PatternRectangular')
        self.assertFalse(control.isBefore)
        self.assertTrue(control.isPromoted)
        self.assertTrue(control.isPromotedByDefault)

    def test_button_is_also_in_the_sketch_create_panel_after_the_native_sketch_pattern(self):
        control = self.ui.sketch_panel.controls.itemById(C.CMD_ID)
        self.assertIsNotNone(control)
        self.assertEqual(control.positionID, 'RectangularSketchPatternCommand')
        self.assertFalse(control.isBefore)
        self.assertTrue(control.isPromoted)

    def test_icons_exist_in_the_resource_folder(self):
        for name in ('16x16.png', '32x32.png'):
            self.assertTrue(os.path.isfile(os.path.join(C.RESOURCES, name)), name)

    def test_stop_removes_buttons_and_definition(self):
        entry.stop(None)
        self.assertIsNone(self.ui.create_panel.controls.itemById(C.CMD_ID))
        self.assertIsNone(self.ui.sketch_panel.controls.itemById(C.CMD_ID))
        self.assertIsNone(self.ui.commandDefinitions.itemById(C.CMD_ID))
        entry.run(None)   # so tearDown's stop has something to remove

    def test_starting_twice_does_not_duplicate_the_buttons(self):
        entry.run(None)
        for panel in (self.ui.create_panel, self.ui.sketch_panel):
            self.assertEqual(sum(1 for c in panel.controls._controls if c.id == C.CMD_ID), 1)

    def test_entry_file_runs_the_way_fusion_loads_add_ins(self):
        # Fusion executes FormulaPattern.py as a module named after its folder, with
        # __path__ set and __package__ unset; relative imports then resolve via __name__.
        folder = os.path.join(ROOT, 'FormulaPattern')
        path = os.path.join(folder, 'FormulaPattern.py')
        namespace = {'__name__': 'FormulaPattern', '__file__': path, '__path__': [folder]}
        with open(path, encoding='utf-8') as f:
            exec(compile(f.read(), path, 'exec'), namespace)
        entry.stop(None)                       # release what the harness installed
        namespace['run'](None)
        self.assertEqual(self.ui.messages, [])
        self.assertIsNotNone(self.ui.create_panel.controls.itemById(C.CMD_ID))
        namespace['stop'](None)
        self.assertIsNone(self.ui.create_panel.controls.itemById(C.CMD_ID))
        entry.run(None)                        # so tearDown has something to stop

    def test_stop_forgets_the_add_in_modules_so_run_loads_edited_code(self):
        # Fusion keeps one Python runtime; without this, Stop + Run in the Scripts and
        # Add-Ins dialog would keep running the old command.py until Fusion restarts.
        before = entry.command
        entry.stop(None)
        for name in ('FormulaPattern.command', 'FormulaPattern.engine', 'FormulaPattern.settings'):
            self.assertNotIn(name, sys.modules)
        entry.run(None)
        self.assertIsNot(entry.command, before)
        self.assertIsNotNone(self.ui.create_panel.controls.itemById(C.CMD_ID))


class DialogLifecycle(FusionHarness):
    """Fusion fires commandCreated before the dialog exists; unitless value inputs (b, c)
    cannot be read or written until the command is activated."""

    def test_dialog_opens_and_reacts_even_before_unitless_inputs_are_ready(self):
        cmd, dialog = self.create_dialog()          # the user's crash happened here
        self.assertEqual(self.ui.messages, [])
        self.assertEqual(len(cmd.inputChanged.handlers), 1, 'input handlers must be attached')
        self.assertEqual(len(cmd.activate.handlers), 1)
        self.assertNotIn('Traceback', self.preview_text(cmd))
        # the reported symptom: changing the object type did nothing
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Faces')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selectionFilters, ['Faces'])

    def test_numbers_fall_back_to_their_defaults_until_the_dialog_is_shown(self):
        cmd, dialog = self.create_dialog()
        self.assertAlmostEqual(dialog.a, 10.0)      # 1 cm in a mm document
        self.assertEqual(dialog.b, 0.0)
        self.assertEqual(dialog.c, 0.0)

    def test_preview_appears_once_the_dialog_is_shown(self):
        cmd, dialog = self.create_dialog()
        cmd.show()
        self.assertIn('10 copies', self.preview_text(cmd))
        self.assertIn('100 mm', self.preview_text(cmd))

    def test_reactivation_keeps_the_users_edits(self):
        # activate fires again when a nested command ends; saved values must not come back
        stored = {'b': '1.5', 'c': '0.25'}
        with mock.patch.object(command, 'load_settings', return_value=stored):
            cmd, dialog = self.open_dialog()
        self.assertAlmostEqual(dialog.b, 1.5)
        self.set_expression(cmd, C.ID_B, '3')
        cmd.show()
        self.assertAlmostEqual(dialog.b, 3.0)
        self.assertAlmostEqual(dialog.c, 0.25)

    def test_invalid_number_after_showing_is_still_reported(self):
        cmd, dialog = self.open_dialog()
        self.set_expression(cmd, C.ID_C, 'abc')
        self.assertIn("'c' is not a valid number: abc", self.preview_text(cmd))
        self.assertFalse(self.validate(cmd))

    def test_an_unexpected_error_shows_in_the_preview_instead_of_killing_the_dialog(self):
        cmd, dialog = self.open_dialog()
        with mock.patch.object(dialog, 'spec', side_effect=RuntimeError('boom')):
            dialog.refresh_preview()
        self.assertIn('boom', self.preview_text(cmd))


class DialogBasics(FusionHarness):
    def test_dialog_opens_with_preview_of_default_formula(self):
        cmd, dialog = self.open_dialog()
        self.assertIn('10 copies', self.preview_text(cmd))
        self.assertIn('100 mm', self.preview_text(cmd))
        self.assertFalse(dialog.circular)

    def test_circular_hides_direction_shows_axis_and_filters_presets(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        inputs = cmd.commandInputs
        self.assertFalse(inputs.itemById(C.ID_DIRECTION).isVisible)
        self.assertTrue(inputs.itemById(C.ID_AXIS).isVisible)
        self.assertEqual(inputs.itemById(C.ID_DIRECTION).getSelectionLimits()[1], 0)
        self.assertEqual(inputs.itemById(C.ID_AXIS).getSelectionLimits()[1], 1)
        self.assertFalse(inputs.itemById(C.ID_A_LEN).isVisible)
        self.assertTrue(inputs.itemById(C.ID_A_ANG).isVisible)
        names = [i.name for i in inputs.itemById(C.ID_PRESET).listItems]
        self.assertNotIn('Guitar frets (12-TET)', names)
        self.assertIn('Golden angle (phyllotaxis)', names)
        modes = [i.name for i in inputs.itemById(C.ID_MODE).listItems]
        self.assertEqual(modes, ['Angle from original', 'Angle step from previous'])
        self.assertIn('°', self.preview_text(cmd))

    def test_switching_back_to_linear_keeps_mode_and_preset(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Growing gaps (geometric)')
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.choose(cmd, C.ID_TYPE, 'Linear')
        self.assertEqual(dialog.preset_id, 'geometric')
        self.assertEqual(dialog.mode, C.engine.MODE_SPACING)

    def test_object_type_change_resets_selection_and_filters(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, 'body')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        sel = cmd.commandInputs.itemById(C.ID_OBJECTS)
        self.assertEqual(sel.selectionCount, 0)
        self.assertEqual(sel.selectionFilters, ['Occurrences'])
        self.assertTrue(sel.hasFocus)


class PresetsAndFormula(FusionHarness):
    def test_fret_preset_fills_formula_and_scale_length(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Guitar frets (12-TET)')
        self.assertEqual(dialog.formula_text, 'a * (1 - 1 / 2^((i - 1) / 12))')
        self.assertAlmostEqual(dialog.a, 648.0)
        self.assertEqual(dialog.mode, C.engine.MODE_POSITION)
        self.assertIn('scale length', cmd.commandInputs.itemById(C.ID_NOTES).text)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 12
        self.changed(cmd, C.ID_COUNT)
        self.assertIn('i = 13: 324 mm', self.preview_text(cmd))   # item 13 is fret 12

    def test_a_accepts_units_in_the_expression(self):
        cmd, dialog = self.open_dialog()
        self.set_expression(cmd, C.ID_A_LEN, '25.5 in')
        self.assertAlmostEqual(dialog.a, 647.7)

    def test_editing_the_formula_switches_preset_to_custom(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Fibonacci gaps')
        self.assertEqual(dialog.preset_id, 'fibonacci')
        self.set_text(cmd, C.ID_FORMULA, 'a * fib(i - 1) + 1')
        self.assertEqual(dialog.preset_id, 'custom')
        self.assertIn('Write any formula', cmd.commandInputs.itemById(C.ID_NOTES).text)

    def test_changing_the_mode_switches_preset_to_custom(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Fibonacci gaps')
        self.choose(cmd, C.ID_MODE, 'Distance from original')
        self.assertEqual(dialog.preset_id, 'custom')

    def test_formula_error_is_shown_and_blocks_ok(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.assertTrue(self.validate(cmd))
        self.set_text(cmd, C.ID_FORMULA, 'a * (i')
        self.assertIn("Can't read the formula", self.preview_text(cmd))
        self.assertTrue(cmd.commandInputs.itemById(C.ID_FORMULA).isValueError)
        self.assertFalse(self.validate(cmd))
        self.set_text(cmd, C.ID_FORMULA, 'a * (i - 1)')
        self.assertFalse(cmd.commandInputs.itemById(C.ID_FORMULA).isValueError)
        self.assertTrue(self.validate(cmd))

    def test_invalid_number_in_b_is_reported(self):
        cmd, dialog = self.open_dialog()
        self.set_expression(cmd, C.ID_B, 'abc')
        self.assertIn("'b' is not a valid number", self.preview_text(cmd))

    def test_user_parameters_are_usable_by_name_in_document_units(self):
        self.design.userParameters.add('scale', 64.8, 'mm')          # 64.8 cm internally = 648 mm
        self.design.userParameters.add('twist', math.pi / 2, 'deg')  # radians internally = 90°
        self.design.userParameters.add('ratio', 1.5, '')
        cmd, dialog = self.open_dialog()
        values = dialog.user_parameter_vars()
        self.assertEqual(sorted(values), ['ratio', 'scale', 'twist'])
        self.assertAlmostEqual(values['scale'], 648.0)
        self.assertAlmostEqual(values['twist'], 90.0)
        self.assertAlmostEqual(values['ratio'], 1.5)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 12
        self.set_text(cmd, C.ID_FORMULA, 'scale * (1 - 1 / 2^((i - 1)/12))')
        self.assertIn('i = 13: 324 mm', self.preview_text(cmd))   # item 13 is fret 12


class LengthUnits(FusionHarness):
    def test_inch_symbol_preference_is_normalised_to_in(self):
        for spelling in ('"', 'inch', 'in'):
            self.design.unitsManager.defaultLengthUnits = spelling
            cmd, dialog = self.open_dialog()
            self.assertEqual(dialog.length_unit, 'in', spelling)
            self.assertEqual(cmd.commandInputs.itemById(C.ID_A_LEN).unitType, 'in', spelling)
            self.ready_linear(cmd)
            self.preview(cmd)
            feats = self.design.rootComponent.features.rectangularPatternFeatures
            self.assertTrue(feats.item(feats.count - 1).input.distanceOne.stringValue.endswith(' in'), spelling)
            self.assertIn(' in', self.preview_text(cmd))

    def test_display_units_enum_is_used_when_the_string_is_unknown(self):
        self.design.unitsManager.defaultLengthUnits = 'furlong'
        self.design.unitsManager.distanceDisplayUnits = adsk.fusion.DistanceUnits.FootDistanceUnits
        cmd, dialog = self.open_dialog()
        self.assertEqual(dialog.length_unit, 'ft')


class Validation(FusionHarness):
    def test_needs_objects_and_a_direction(self):
        cmd, dialog = self.open_dialog()
        self.assertFalse(self.validate(cmd))
        self.select(cmd, C.ID_OBJECTS, 'slot')
        self.assertFalse(self.validate(cmd))
        self.select(cmd, C.ID_DIRECTION, 'edge')
        self.assertTrue(self.validate(cmd))

    def test_circular_needs_an_axis_not_a_direction(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, 'rib')
        self.select(cmd, C.ID_DIRECTION, 'edge')
        self.choose(cmd, C.ID_TYPE, 'Circular')     # the direction is hidden now and does not count
        self.assertFalse(self.validate(cmd))
        self.select(cmd, C.ID_AXIS, 'cylinder')
        self.assertTrue(self.validate(cmd))


class Building(FusionHarness):
    def test_preview_builds_one_rectangular_pattern_per_copy_in_a_named_group(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.choose(cmd, C.ID_PRESET, 'Guitar frets (12-TET)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 12
        self.changed(cmd, C.ID_COUNT)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult)

        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.count, 12)
        first, last = feats.item(0), feats.item(11)
        self.assertEqual(first.input.quantityOne.realValue, 2.0)
        self.assertEqual(first.input.patternDistanceType, adsk.fusion.PatternDistanceType.ExtentPatternDistanceType)
        self.assertEqual(first.input.distanceOne.stringValue, '36.3694454 mm')
        self.assertEqual(last.input.distanceOne.stringValue, '324 mm')
        self.assertEqual(first.input.directionOneEntity.name, 'neck edge')
        self.assertEqual(first.input.inputEntities.item(0).name, 'slot')
        self.assertEqual(first.name, 'Formula Pattern 1/12')
        self.assertEqual(last.name, 'Formula Pattern 12/12')
        self.assertEqual(first.attributes.itemByName('FormulaPattern', 'copy').value, '1')
        stored = json.loads(first.attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['preset'], 'frets')

        groups = self.design.timeline.timelineGroups
        self.assertEqual(groups.count, 1)
        self.assertEqual((groups.item(0).start, groups.item(0).end), (0, 11))
        self.assertEqual(groups.item(0).name, 'Formula Pattern')

    def test_a_linear_pattern_of_a_body_makes_one_body_per_copy(self):
        # Fusion starts a RectangularPatternFeatureInput with quantityTwo = 3 (seen 2026-11-08:
        # 4 copies of a louvre made 20 bodies, 3 on every copy and 2 on the original).
        root = self.design.rootComponent
        louvre = root.body('Louvre')
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, louvre)
        self.select(cmd, C.ID_DIRECTION, 'neck edge')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 4
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual([b.name for b in root.bRepBodies],
                         ['Louvre', 'Louvre (1)', 'Louvre (2)', 'Louvre (3)', 'Louvre (4)'])
        first = root.features.rectangularPatternFeatures.item(0).input
        self.assertEqual(first.quantityTwo.realValue, 1.0, 'one row only')

    def test_flip_builds_negative_distances(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        cmd.commandInputs.itemById(C.ID_FLIP).value = True
        self.changed(cmd, C.ID_FLIP)
        self.preview(cmd)
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.item(0).input.distanceOne.stringValue, '-10 mm')

    def test_circular_builds_circular_patterns_with_total_angle(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, 'rib')
        self.select(cmd, C.ID_AXIS, 'cylinder')
        self.choose(cmd, C.ID_PRESET, 'Golden angle (phyllotaxis)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult)
        feats = self.design.rootComponent.features.circularPatternFeatures
        self.assertEqual(feats.count, 3)
        self.assertEqual(feats.item(0).input.axis.name, 'cylinder')
        self.assertEqual(feats.item(0).input.quantity.realValue, 2.0)
        self.assertEqual(feats.item(0).input.totalAngle.stringValue, '137.508 deg')
        self.assertEqual(feats.item(2).input.totalAngle.stringValue, '52.524 deg')
        self.assertFalse(feats.item(0).input.isSymmetric)

    def test_compute_option_is_passed_through(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.choose(cmd, C.ID_COMPUTE, 'Identical')
        self.preview(cmd)
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.item(0).input.patternComputeOption,
                         adsk.fusion.PatternComputeOptions.IdenticalPatternCompute)

    def test_timeline_group_uses_the_custom_name(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.set_text(cmd, C.ID_GROUP_NAME, 'Louvres')
        self.preview(cmd)
        self.assertEqual(self.design.timeline.timelineGroups.item(0).name, 'Louvres')
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.item(0).name, 'Louvres 1/10')

    def test_single_copy_is_not_grouped(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 1
        self.changed(cmd, C.ID_COUNT)
        self.preview(cmd)
        self.assertEqual(self.design.timeline.timelineGroups.count, 0)

    def test_preview_is_skipped_when_inputs_are_incomplete(self):
        cmd, dialog = self.open_dialog()
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 0)
        self.assertNotIn('✖', self.preview_text(cmd))

    def test_preview_is_skipped_when_show_in_model_is_off_but_execute_builds(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        cmd.commandInputs.itemById(C.ID_LIVE).value = False
        self.changed(cmd, C.ID_LIVE)
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.count, 0)
        cmd.execute.fire(adsk.core.CommandEventArgs(cmd))
        self.assertEqual(feats.count, 10)

    def test_preview_is_skipped_above_the_copy_limit_and_says_so(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        cmd.commandInputs.itemById(C.ID_COUNT).value = C.MAX_MODEL_PREVIEW + 1
        self.changed(cmd, C.ID_COUNT)
        self.assertIn('Model preview is off', self.preview_text(cmd))
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)

    def test_fusion_failure_is_shown_in_preview_and_then_cleared(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        feats.fail_at = 3
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('could not create copy 3 of 10 at 30 mm', self.preview_text(cmd))
        feats.fail_at = None
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult)
        self.assertNotIn('could not create', self.preview_text(cmd))

    def test_fusion_rejecting_a_feature_argument_names_what_was_selected(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Features')
        self.select(cmd, C.ID_OBJECTS, adsk.fusion.Entity('slot cut', 'ExtrudeFeature'))
        self.select(cmd, C.ID_DIRECTION, adsk.fusion.Entity('neck line', 'SketchLine'))
        self.design.rootComponent.features.rectangularPatternFeatures.reject = 'directionOneEntity'
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        text = self.preview_text(cmd)
        self.assertIn('invalid argument directionOneEntity', text)
        self.assertIn("direction: SketchLine 'neck line'", text)
        self.assertIn('objects: 1 × ExtrudeFeature', text)

    def test_execute_failure_reports_through_fusion(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.design.rootComponent.features.rectangularPatternFeatures.fail_at = 1
        args = cmd.execute.fire(adsk.core.CommandEventArgs(cmd))
        self.assertTrue(args.executeFailed)
        self.assertIn('copy 1 of 10', args.executeFailedMessage)


class Settings(FusionHarness):
    def test_settings_are_saved_on_ok_and_not_on_cancel(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Growing gaps (geometric)')
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CancelledTerminationReason))
        self.assertEqual(self.saved, [])
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Growing gaps (geometric)')
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(self.saved[0]['preset'], 'geometric')
        self.assertEqual(self.saved[0]['formula'], 'a * b^(i - 2)')
        self.assertEqual(self.saved[0]['b'], '1.2')
        self.assertEqual(self.saved[0]['numbering'], 'item', 'the file says which numbering its formulas use')

    def test_settings_are_restored_next_time_without_reapplying_the_preset(self):
        stored = {
            'pattern_type': 'circular', 'object_type': 'Features', 'count': 7, 'preset': 'geometric',
            'mode': 'spacing', 'formula': 'a * b^(i - 2) + c', 'a_angle': '12 deg', 'b': '1.1', 'c': '0.5',
            'flip': True, 'live': False, 'compute': 2, 'group_name': 'Fins', 'numbering': 'item',
        }
        with mock.patch.object(command, 'load_settings', return_value=stored):
            cmd, dialog = self.open_dialog()
        self.assertTrue(dialog.circular)
        self.assertEqual(dialog.object_type, 'Features')
        self.assertEqual(dialog.count, 7)
        self.assertEqual(dialog.preset_id, 'geometric')
        self.assertEqual(dialog.mode, 'spacing')
        self.assertEqual(dialog.formula_text, 'a * b^(i - 2) + c')
        self.assertAlmostEqual(dialog.a, 12.0)
        self.assertAlmostEqual(dialog.b, 1.1)
        self.assertAlmostEqual(dialog.c, 0.5)
        self.assertTrue(dialog.flip)
        self.assertFalse(dialog.live)
        self.assertEqual(dialog.compute_option, adsk.fusion.PatternComputeOptions.AdjustPatternCompute)
        self.assertEqual(dialog.group_name, 'Fins')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selectionFilters, ['Features'])

    def test_remembered_values_from_1_2_are_renumbered_so_the_pattern_stays_the_same(self):
        # 1.2.x numbered the copies 1 … n in the formulas and wrote no 'numbering' key; since
        # 1.3 the original is item 1, so every bare i comes back as (i - 1): same geometry.
        stored = {
            'pattern_type': 'linear', 'object_type': 'Bodies', 'count': 4, 'preset': 'custom',
            'mode': 'position', 'formula': 'a * i', 'rotation': '30 * i', 'size': '1 + 0.1 * i',
            'depth': 'a * ((i * i) % 7) / 7',
        }
        with mock.patch.object(command, 'load_settings', return_value=stored):
            cmd, dialog = self.open_dialog()
        self.assertEqual(dialog.formula_text, 'a * (i - 1)')
        self.assertEqual(dialog.rotation_text, '30 * (i - 1)')
        self.assertEqual(dialog.size_text, '1 + 0.1 * (i - 1)')
        self.assertEqual(dialog.depth_text, 'a * (((i - 1) * (i - 1)) % 7) / 7')
        self.assertEqual(dialog.preset_id, 'custom')
        text = self.preview_text(cmd)
        self.assertIn('i = 2: 10 mm · turn 30° · ×1.1', text, 'the first copy sits where 1.2 put it')
        self.assertIn('i = 5: 40 mm · turn 120° · ×1.4', text)
        self.assertNotIn('i = 1:', text)
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))
        self.assertEqual(self.saved[0]['formula'], 'a * (i - 1)')
        self.assertEqual(self.saved[0]['rotation'], '30 * (i - 1)')
        self.assertEqual(self.saved[0]['numbering'], 'item', 'written back in the new numbering')

    def test_garbage_settings_are_ignored(self):
        with mock.patch.object(command, 'load_settings', return_value={'count': 'lots', 'pattern_type': 'spiral', 'preset': 'gone'}):
            cmd, dialog = self.open_dialog()
        self.assertEqual(dialog.count, 10)
        self.assertFalse(dialog.circular)
        self.assertEqual(dialog.preset_id, 'custom')

    def test_destroy_releases_the_dialog_handlers(self):
        cmd, dialog = self.open_dialog()
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))
        self.assertFalse(any(getattr(h, 'dialog', None) is dialog for h in command._handlers))
        self.assertEqual(len(command._handlers), 1)   # the command-created handler stays


class SketchMode(FusionHarness):
    """Patterning sketch curves: one sketch pattern constraint per copy."""

    def setUp(self):
        super().setUp()
        self.sketch = adsk.fusion.Sketch('Fretboard')
        self.fret = self.sketch.line('fret slot')
        self.marker = self.sketch.point('marker')
        self.axis_line = self.sketch.line('centre line')

    def open_in_sketch(self):
        self.app.activeEditObject = self.sketch
        return self.open_dialog()

    def test_sketch_curves_are_the_default_while_editing_a_sketch(self):
        cmd, dialog = self.open_in_sketch()
        inputs = cmd.commandInputs
        self.assertEqual(dialog.object_type, 'Sketch curves')
        self.assertEqual(inputs.itemById(C.ID_OBJECTS).selectionFilters, ['SketchCurves', 'SketchPoints'])
        self.assertEqual(inputs.itemById(C.ID_DIRECTION).selectionFilters, ['SketchLines'])
        self.assertEqual(inputs.itemById(C.ID_DIRECTION).getSelectionLimits()[1], 0, 'direction is optional')
        self.assertIn('X axis', inputs.itemById(C.ID_DIRECTION).commandPrompt)
        self.assertEqual(inputs.itemById(C.ID_AXIS).selectionFilters, ['SketchPoints', 'SketchCircles'])
        self.assertFalse(inputs.itemById(C.ID_ADVANCED_GROUP).isVisible)

    def test_active_sketch_wins_over_the_remembered_object_type(self):
        with mock.patch.object(command, 'load_settings', return_value={'object_type': 'Features'}):
            cmd, dialog = self.open_in_sketch()
        self.assertEqual(dialog.object_type, 'Sketch curves')

    def test_bodies_stay_the_default_outside_a_sketch(self):
        cmd, dialog = self.open_dialog()
        self.assertEqual(dialog.object_type, 'Bodies')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_DIRECTION).getSelectionLimits()[1], 1)

    def test_choosing_sketch_curves_outside_a_sketch_switches_the_filters_and_back(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_DIRECTION, 'edge')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Sketch curves')
        direction = cmd.commandInputs.itemById(C.ID_DIRECTION)
        self.assertEqual(direction.selectionFilters, ['SketchLines'])
        self.assertEqual(direction.selectionCount, 0, 'a model edge cannot drive a sketch pattern')
        self.assertEqual(direction.getSelectionLimits()[1], 0)
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Bodies')
        self.assertEqual(direction.selectionFilters, list(C.DIRECTION_FILTERS))
        self.assertEqual(direction.getSelectionLimits()[1], 1)
        self.assertEqual(cmd.commandInputs.itemById(C.ID_AXIS).selectionFilters, list(C.AXIS_FILTERS))
        self.assertTrue(cmd.commandInputs.itemById(C.ID_ADVANCED_GROUP).isVisible)

    def test_linear_sketch_pattern_builds_one_constraint_per_copy(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret, self.marker)
        self.select(cmd, C.ID_DIRECTION, self.axis_line)
        self.choose(cmd, C.ID_PRESET, 'Guitar frets (12-TET)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))

        made = self.sketch.geometricConstraints.created
        self.assertEqual(len(made), 3)
        first, last = made[0].input, made[2].input
        self.assertEqual(first.entities, [self.fret, self.marker])
        self.assertIs(first.directionOneEntity, self.axis_line)
        self.assertEqual(first.quantityOne.realValue, 2.0)
        self.assertEqual(first.distanceType, adsk.fusion.PatternDistanceType.ExtentPatternDistanceType)
        self.assertEqual(first.distanceOne.stringValue, '36.3694454 mm')
        self.assertEqual(last.distanceOne.stringValue, '103.099123 mm')
        self.assertEqual(first.quantityTwo.realValue, 1.0, 'no second direction')
        self.assertIsNone(first.directionTwoEntity, 'never passed as None: Fusion rejects that')
        self.assertIsNotNone(first.distanceTwo)
        self.assertEqual(made[0].attributes.itemByName('FormulaPattern', 'copy').value, '1')
        self.assertEqual(self.design.timeline.timelineGroups.count, 0, 'sketch constraints are not timeline items')
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 0)

    def test_linear_sketch_pattern_without_a_direction_uses_the_sketch_x_axis(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret)
        self.assertTrue(self.validate(cmd))
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))
        made = self.sketch.geometricConstraints.created[0].input
        self.assertIsNone(made.directionOneEntity)
        self.assertEqual(made.quantityOne.realValue, 2.0)
        self.assertEqual(made.distanceOne.stringValue, '10 mm')
        self.assertEqual(made.quantityTwo.realValue, 1.0)

    def test_flipped_sketch_pattern_uses_negative_distances(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret)
        cmd.commandInputs.itemById(C.ID_FLIP).value = True
        self.changed(cmd, C.ID_FLIP)
        self.preview(cmd)
        self.assertEqual(self.sketch.geometricConstraints.created[0].input.distanceOne.stringValue, '-10 mm')

    def test_circular_sketch_pattern_around_a_point_or_a_circle_centre(self):
        cmd, dialog = self.open_in_sketch()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, self.fret)
        self.assertFalse(self.validate(cmd), 'circular needs a centre even in a sketch')
        centre = self.sketch.point('hub')
        self.select(cmd, C.ID_AXIS, centre)
        self.choose(cmd, C.ID_PRESET, 'Golden angle (phyllotaxis)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))
        made = self.sketch.geometricConstraints.created
        self.assertEqual(len(made), 2)
        self.assertIs(made[0].input.centerPoint, centre)
        self.assertEqual(made[0].input.quantity.realValue, 2.0)
        self.assertEqual(made[0].input.totalAngle.stringValue, '137.508 deg')
        self.assertEqual(made[1].input.totalAngle.stringValue, '275.016 deg')
        self.assertFalse(made[0].input.isSymmetric)

        wheel = self.sketch.circle('wheel')
        cmd.commandInputs.itemById(C.ID_AXIS).clearSelection()
        self.select(cmd, C.ID_AXIS, wheel)
        self.preview(cmd)
        self.assertIs(made[-1].input.centerPoint, wheel.centerSketchPoint)

    def test_curves_from_two_sketches_are_refused_with_a_clear_message(self):
        cmd, dialog = self.open_in_sketch()
        other = adsk.fusion.Sketch('Other')
        self.select(cmd, C.ID_OBJECTS, self.fret, other.line('stray'))
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('same sketch', self.preview_text(cmd))
        self.assertEqual(self.sketch.geometricConstraints.created, [])

    def test_direction_from_another_sketch_is_refused(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret)
        self.select(cmd, C.ID_DIRECTION, adsk.fusion.Sketch('Other').line('stray'))
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('same sketch', self.preview_text(cmd))

    def test_entities_selected_through_an_occurrence_are_resolved_to_the_sketch_objects(self):
        # Fusion hands out proxy objects for geometry selected inside an occurrence; the
        # sketch pattern API only accepts the sketch's own (native) objects.
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret.proxy('Neck:1'))
        self.select(cmd, C.ID_DIRECTION, self.axis_line.proxy('Neck:1'))
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))
        made = self.sketch.geometricConstraints.created[0].input
        self.assertIs(made.directionOneEntity, self.axis_line)
        self.assertEqual(made.entities, [self.fret])

    def test_fusion_rejecting_the_direction_names_what_was_selected(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret)
        stray = self.axis_line
        with mock.patch.object(command, '_native', side_effect=lambda e: e):   # keep the proxy
            self.select(cmd, C.ID_DIRECTION, stray.proxy('Neck:1'))
            args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        text = self.preview_text(cmd)
        self.assertIn('invalid argument directionOneEntity', text)
        self.assertIn("direction: SketchLine 'centre line' in sketch 'Fretboard' via Neck:1", text)
        self.assertIn("objects: 1 × SketchLine", text)

    def test_fusion_refusing_a_sketch_copy_is_reported(self):
        cmd, dialog = self.open_in_sketch()
        self.select(cmd, C.ID_OBJECTS, self.fret)
        self.sketch.geometricConstraints.fail_at = 2
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('could not create copy 2 of 10 at 20 mm', self.preview_text(cmd))

    def test_sketch_curves_outside_a_sketch_still_pattern_into_their_own_sketch(self):
        cmd, dialog = self.open_dialog()           # no sketch being edited
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Sketch curves')
        self.select(cmd, C.ID_OBJECTS, self.fret)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))
        self.assertEqual(len(self.sketch.geometricConstraints.created), 10)

    def test_sketch_object_type_is_remembered(self):
        cmd, dialog = self.open_in_sketch()
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))
        self.assertEqual(self.saved[0]['object_type'], 'Sketch curves')


class EditingSavedPattern(FusionHarness):
    """Pick a saved Formula Pattern from the "Saved pattern" list (or click one of its copies):
    the dialog loads its formula, values, objects and direction, highlights its copies, and
    OK replaces the pattern in place once something was changed."""

    def finish(self, cmd):
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))

    def pick(self, cmd, label):
        self.choose(cmd, C.ID_PATTERN_LIST, label)

    def list_names(self, cmd):
        return [item.name for item in cmd.commandInputs.itemById(C.ID_PATTERN_LIST).listItems]

    def highlighted(self, cmd):
        sel = cmd.commandInputs.itemById(C.ID_EXISTING)
        return [sel.selection(k).entity for k in range(sel.selectionCount)]

    def status(self, cmd):
        return cmd.commandInputs.itemById(C.ID_EDIT_STATUS).text

    # ---- the dialog around editing ----

    def test_edit_group_comes_first_with_the_copies_and_status_hidden_until_a_pattern_is_picked(self):
        self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        inputs = cmd.commandInputs
        self.assertEqual(inputs.item(0).id, C.ID_EDIT_GROUP, 'new or saved pattern is the first choice')
        self.assertTrue(inputs.itemById(C.ID_OBJECTS).hasFocus, 'clicks go to Objects, not to Copies')
        self.assertFalse(inputs.itemById(C.ID_EXISTING).isVisible)
        self.assertFalse(inputs.itemById(C.ID_EDIT_STATUS).isVisible)
        self.pick(cmd, 'Frets (3 copies)')
        self.assertTrue(inputs.itemById(C.ID_EXISTING).isVisible)
        self.assertTrue(inputs.itemById(C.ID_EDIT_STATUS).isVisible)
        self.assertTrue(inputs.itemById(C.ID_OBJECTS).hasFocus, 'focus is back on Objects after loading')
        self.pick(cmd, 'New pattern')
        self.assertFalse(inputs.itemById(C.ID_EXISTING).isVisible)
        self.assertFalse(inputs.itemById(C.ID_EDIT_STATUS).isVisible)

    def test_status_says_what_ok_will_do(self):
        self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.assertIn('Editing Frets (3 copies)', self.status(cmd))
        self.assertIn('Change anything', self.status(cmd))
        self.assertTrue(self.validate(cmd), 'OK is available right away')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 4
        self.changed(cmd, C.ID_COUNT)
        self.assertIn('OK replaces it', self.status(cmd))
        self.assertNotIn('Change anything', self.status(cmd))

    def test_preview_group_is_collapsed_until_an_error_needs_showing(self):
        cmd, dialog = self.open_dialog()
        group = cmd.commandInputs.itemById(C.ID_PREVIEW_GROUP)
        self.assertFalse(group.isExpanded)
        self.ready_linear(cmd)
        self.assertFalse(group.isExpanded)
        self.set_text(cmd, C.ID_FORMULA, 'a * (')
        self.assertTrue(group.isExpanded, 'an error must be seen')

    def test_objects_in_a_component_are_reselected_in_the_root_context(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        slot = adsk.fusion.Entity('slot', 'BRepBody', neck)
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, slot.createForAssemblyContext(self.design.rootComponent.allOccurrencesByComponent(neck).item(0)))
        self.select(cmd, C.ID_DIRECTION, 'neck edge')
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        # What the pattern feature reads back is the body in its own component; Fusion's
        # selection inputs want the proxy the user would have clicked.
        feature = self.design.rootComponent.features.rectangularPatternFeatures.item(0)
        feature.input.inputEntities = adsk.core.ObjectCollection.create()
        feature.input.inputEntities.add(slot)

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Formula Pattern (10 copies)')
        selected = cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity
        self.assertIs(selected.nativeObject, slot)
        self.assertEqual(selected.assemblyContext.name, 'Neck:1')
        self.assertTrue(self.validate(cmd))

    def test_faces_and_edges_in_a_component_are_reselected_through_their_body(self):
        # Seen in Fusion 2026-11-06: a Faces pattern in "Fret Jig:1" loaded with Objects and
        # Direction empty. A face or edge has no parentComponent; its body has.
        neck = self.design.add_component('Neck', 'Neck:1')
        body = adsk.fusion.Entity('Body1', 'BRepBody', neck)
        face = adsk.fusion.Entity('slot face', 'BRepFace', body=body)
        edge = adsk.fusion.Entity('neck edge', 'BRepEdge', body=body)
        occurrence = self.design.rootComponent.allOccurrencesByComponent(neck).item(0)
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Faces')
        self.select(cmd, C.ID_OBJECTS, face.createForAssemblyContext(occurrence))
        self.select(cmd, C.ID_DIRECTION, edge.createForAssemblyContext(occurrence))
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        feature = self.design.rootComponent.features.rectangularPatternFeatures.item(0)
        feature.input.inputEntities = adsk.core.ObjectCollection.create()
        feature.input.inputEntities.add(face)              # read back native, as Fusion does
        feature.input.directionOneEntity = edge

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Formula Pattern (10 copies)')
        objects = cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity
        direction = cmd.commandInputs.itemById(C.ID_DIRECTION).selection(0).entity
        self.assertIs(objects.nativeObject, face)
        self.assertEqual(objects.assemblyContext.name, 'Neck:1')
        self.assertIs(direction.nativeObject, edge)
        self.assertEqual(direction.assemblyContext.name, 'Neck:1')
        self.assertTrue(self.validate(cmd))

    def test_a_selection_fusion_refused_while_loading_is_completed_before_ok_is_judged(self):
        self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        cmd.commandInputs.itemById(C.ID_OBJECTS).fail_next_add = 1
        self.pick(cmd, 'Frets (3 copies)')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selectionCount, 0)
        self.assertTrue(self.validate(cmd), 'validateInputs retries the selection')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity.name, 'slot')
        self.assertNotIn('could not', self.status(cmd))

    def test_a_selection_fusion_keeps_refusing_is_asked_of_the_user(self):
        self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        cmd.commandInputs.itemById(C.ID_OBJECTS).fail_next_add = 3     # the load and two retries
        self.pick(cmd, 'Frets (3 copies)')
        self.assertFalse(self.validate(cmd))
        self.assertFalse(self.validate(cmd))
        self.assertIn('could not re-select', self.status(cmd))
        self.assertIn('Click its objects', self.status(cmd))
        self.select(cmd, C.ID_OBJECTS, 'slot')
        self.assertTrue(self.validate(cmd))
        self.assertNotIn('could not', self.status(cmd))

    # ---- the list ----

    def test_the_list_offers_every_saved_pattern_in_timeline_order(self):
        self.make_linear(count=3, group='Frets')
        self.make_linear(count=2, group='Louvres')
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Frets (3 copies)', 'Louvres (2 copies)'])
        self.assertIsNone(dialog.editing)
        self.assertEqual(self.highlighted(cmd), [])

    def test_choosing_from_the_list_loads_the_pattern_and_highlights_its_copies(self):
        feats = self.make_linear(count=4, group='Frets')
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')          # the dialog must switch back to linear
        self.pick(cmd, 'Frets (4 copies)')
        self.assertTrue(dialog.editing)
        self.assertFalse(dialog.circular)
        self.assertEqual(dialog.preset_id, 'frets')
        self.assertEqual(dialog.count, 4)
        self.assertEqual(dialog.group_name, 'Frets')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity.name, 'slot')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_DIRECTION).selection(0).entity.name, 'neck edge')
        self.assertEqual(self.highlighted(cmd), feats, 'every copy is highlighted')
        self.assertIn('Editing Frets (4 copies)', self.status(cmd))
        self.assertTrue(self.validate(cmd))
        timeline = self.design.timeline
        self.assertEqual(timeline.markerPosition, timeline.count, 'reading the inputs rolls the timeline; it is put back')

    def test_reading_the_inputs_puts_the_marker_back_where_the_user_had_it(self):
        other = self.design.rootComponent.features.otherFeatures
        other.add('Base extrude')
        self.make_linear(count=3, group='Frets')
        other.add('Fillet1')
        timeline = self.design.timeline
        timeline.markerPosition = 2            # before Fillet1
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity.name, 'slot')
        self.assertEqual(timeline.markerPosition, 2)

    def test_choosing_new_pattern_drops_the_edit_and_the_highlight(self):
        old = self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.pick(cmd, 'New pattern')
        self.assertIsNone(dialog.editing)
        self.assertEqual(self.highlighted(cmd), [])
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertFalse(any(f._deleted for f in old))
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 6)

    def test_clicking_a_copy_of_another_pattern_switches_to_it(self):
        feats = self.make_linear(count=3, group='Frets')
        self.make_linear(count=2, group='Louvres')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Louvres (2 copies)')
        self.click_copy(cmd, feats[1])
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name, 'Frets (3 copies)')
        self.assertEqual(self.highlighted(cmd), feats)
        self.assertEqual(dialog.editing.id, feats[0].attributes.itemByName('FormulaPattern', 'pattern').value)
        self.assertEqual(dialog.count, 3)
        self.assertTrue(cmd.commandInputs.itemById(C.ID_OBJECTS).hasFocus)

    def test_nothing_is_rebuilt_until_something_changes(self):
        old = self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.assertFalse(self.preview(cmd).isValidResult, 'unchanged: nothing to rebuild')
        self.assertFalse(any(f._deleted for f in old))
        self.assertEqual(len(self.highlighted(cmd)), 3, 'the highlight stays')
        cmd.execute.fire(adsk.core.CommandEventArgs(cmd))     # OK without changes
        self.assertFalse(any(f._deleted for f in old))
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 3)

        cmd.commandInputs.itemById(C.ID_COUNT).value = 5
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(f._deleted for f in old))
        self.assertEqual(self.highlighted(cmd), [], 'deleted copies are not left in the highlight')
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 5)

    def test_sketch_patterns_of_the_sketch_being_edited_are_listed(self):
        sketch = adsk.fusion.Sketch('Fretboard')
        fret, axis_line = sketch.line('fret slot'), sketch.line('centre line')
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, fret)
        self.select(cmd, C.ID_DIRECTION, axis_line)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        constraints = list(sketch.geometricConstraints.created)

        self.app.activeEditObject = None                 # not listed outside its sketch
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern'])
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CancelledTerminationReason))

        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Fretboard sketch pattern (3 copies)'])
        self.pick(cmd, 'Fretboard sketch pattern (3 copies)')
        self.assertEqual(dialog.object_type, 'Sketch curves')
        self.assertEqual(dialog.count, 3)
        self.assertIs(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity, fret)
        self.assertIs(cmd.commandInputs.itemById(C.ID_DIRECTION).selection(0).entity, axis_line)
        self.assertEqual(self.highlighted(cmd), [e for c in constraints for e in c.createdEntities])

    def test_the_designs_patterns_are_not_offered_inside_a_sketch(self):
        # Editing a sketch rolls the timeline back to the sketch (Fusion 2026-11-08): the
        # design's patterns after it are rolled back there (a component pattern cannot even
        # be found), and replacing one before it would roll the timeline back past the open
        # sketch. So inside a sketch only the sketch's own patterns are offered.
        old = self.make_linear(count=3, group='Frets')
        sketch = adsk.fusion.Sketch('Later')
        fret, axis_line = sketch.line('fret slot'), sketch.line('centre line')
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern'])
        self.select(cmd, C.ID_OBJECTS, fret)
        self.select(cmd, C.ID_DIRECTION, axis_line)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)

        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Later sketch pattern (2 copies)'])
        self.pick(cmd, 'Later sketch pattern (2 copies)')
        highlighted = self.highlighted(cmd)
        self.click_copy(cmd, old[1])          # a copy of the design's pattern: refused, the edit stays
        self.assertIn('Finish the sketch first', self.preview_text(cmd))
        self.assertEqual(self.highlighted(cmd), highlighted)
        self.assertEqual(dialog.object_type, 'Sketch curves')
        self.assertFalse(self.preview(cmd).isValidResult)

    def test_patterns_are_found_from_the_timeline_when_findattributes_fails(self):
        old = self.make_linear(count=3, group='Frets')

        def broken(group, name):
            raise RuntimeError('2 : InternalValidationError : res')
        self.design.findAttributes = broken
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Frets (3 copies)'])
        self.pick(cmd, 'Frets (3 copies)')
        self.assertEqual(self.highlighted(cmd), old)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(f._deleted for f in old))
        self.assertEqual(self.design.rootComponent.features.rectangularPatternFeatures.count, 2)

    def make_linear(self, count=3, group='Frets', preset='Guitar frets (12-TET)'):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.choose(cmd, C.ID_PRESET, preset)
        cmd.commandInputs.itemById(C.ID_COUNT).value = count
        self.changed(cmd, C.ID_COUNT)
        self.set_text(cmd, C.ID_GROUP_NAME, group)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        return list(self.design.rootComponent.features.rectangularPatternFeatures.created)

    def click_copy(self, cmd, entity):
        """Click a copy into the Copies input (shown once a pattern is being edited)."""
        self.select(cmd, C.ID_EXISTING, entity)

    # ---- loading ----

    def test_every_copy_carries_the_pattern_id_the_edit_path_looks_for(self):
        feats = self.make_linear()
        ids = {f.attributes.itemByName('FormulaPattern', 'pattern').value for f in feats}
        self.assertEqual(len(ids), 1)
        self.assertTrue(next(iter(ids)))

    def test_loading_a_circular_pattern_switches_the_type_and_fills_the_axis(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, 'rib')
        self.select(cmd, C.ID_AXIS, 'cylinder')
        self.choose(cmd, C.ID_PRESET, 'Golden angle (phyllotaxis)')
        self.preview(cmd)
        self.finish(cmd)

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Formula Pattern (10 copies)')
        self.assertTrue(dialog.circular)
        self.assertEqual(dialog.preset_id, 'golden_angle')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity.name, 'rib')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_AXIS).selection(0).entity.name, 'cylinder')
        self.assertTrue(self.validate(cmd))

    def test_a_stray_click_into_copies_is_reported_and_the_edit_goes_on(self):
        feats = self.make_linear(count=3, group='Frets')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 5
        self.changed(cmd, C.ID_COUNT)
        self.click_copy(cmd, self.design.rootComponent.features.otherFeatures.add('Fillet1'))
        self.assertTrue(dialog.editing, 'the edit is kept')
        self.assertTrue(dialog.dirty, 'and so are the changes')
        self.assertEqual(dialog.count, 5)
        self.assertIn('not a copy made by Formula Pattern', self.preview_text(cmd))
        self.assertEqual(self.highlighted(cmd), feats, 'the stray entity is dropped from the highlight')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name, 'Frets (3 copies)')
        self.assertTrue(self.validate(cmd))
        self.assertTrue(cmd.commandInputs.itemById(C.ID_OBJECTS).hasFocus)

    def test_clicking_a_copied_curve_of_another_sketch_pattern_switches_to_it(self):
        sketch = adsk.fusion.Sketch('Sketch1')
        first, second = sketch.line('first'), sketch.line('second')
        self.app.activeEditObject = sketch
        for line in (first, second):
            cmd, dialog = self.open_dialog()
            self.select(cmd, C.ID_OBJECTS, line)
            cmd.commandInputs.itemById(C.ID_COUNT).value = 2
            self.changed(cmd, C.ID_COUNT)
            self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
            self.finish(cmd)
        constraints = sketch.geometricConstraints.created
        copies_of_second = [e for c in constraints[2:] for e in c.createdEntities]

        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd),
                         ['New pattern', 'Sketch1 sketch pattern (2 copies)', 'Sketch1 sketch pattern (2 copies) #2'])
        self.pick(cmd, 'Sketch1 sketch pattern (2 copies)')
        self.assertIs(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity, first)
        self.click_copy(cmd, copies_of_second[1])
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name,
                         'Sketch1 sketch pattern (2 copies) #2')
        self.assertIs(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity, second)
        self.assertEqual(self.highlighted(cmd), copies_of_second)
        self.click_copy(cmd, first)                      # the original of the first pattern
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name,
                         'Sketch1 sketch pattern (2 copies)')

    def test_a_copy_made_by_an_older_version_says_so(self):
        self.make_linear(count=3, group='Frets')
        old = self.design.rootComponent.features.otherFeatures.add('Formula Pattern 1/3')
        old.attributes.add('FormulaPattern', 'settings', '{}')
        old.attributes.add('FormulaPattern', 'copy', '1')
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Frets (3 copies)'], 'no id: not listed')
        self.pick(cmd, 'Frets (3 copies)')
        self.click_copy(cmd, old)
        self.assertIn('older version', self.preview_text(cmd))
        self.assertEqual(dialog.editing.label, 'Frets (3 copies)', 'the edit in progress is kept')

    # ---- rebuilding ----

    def test_ok_replaces_the_pattern_in_place_with_the_new_count(self):
        other = self.design.rootComponent.features.otherFeatures
        other.add('Base extrude')
        old = self.make_linear(count=3, group='Frets')
        other.add('Fillet1')
        timeline = self.design.timeline
        self.assertEqual(timeline.names(), ['Base extrude', ['Frets 1/3', 'Frets 2/3', 'Frets 3/3'], 'Fillet1'])
        old_id = old[0].attributes.itemByName('FormulaPattern', 'pattern').value

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 5
        self.changed(cmd, C.ID_COUNT)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))

        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual([f.name for f in feats.created], [f'Frets {k}/5' for k in range(1, 6)])
        self.assertTrue(all(f._deleted for f in old), 'the old copies are gone')
        self.assertEqual(timeline.names(), ['Base extrude', [f'Frets {k}/5' for k in range(1, 6)], 'Fillet1'])
        self.assertEqual(timeline.markerPosition, timeline.count, 'marker back at the end')
        self.assertEqual(timeline.timelineGroups.count, 1)
        self.assertEqual(timeline.timelineGroups.item(0).name, 'Frets')
        new = feats.item(0).attributes
        self.assertEqual(new.itemByName('FormulaPattern', 'pattern').value, old_id, 'same pattern, edited')
        self.assertEqual(json.loads(new.itemByName('FormulaPattern', 'settings').value)['count'], 5)

    def test_a_marker_the_user_rolled_back_stays_where_it_was(self):
        other = self.design.rootComponent.features.otherFeatures
        other.add('Base extrude')
        old = self.make_linear(count=3)
        other.add('Fillet1')
        timeline = self.design.timeline
        timeline.markerPosition = 2            # before Fillet1
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(timeline.markerPosition, 2)
        self.assertEqual(timeline.names()[-1], 'Fillet1')

    def test_a_single_copy_pattern_outside_any_group_is_replaced_too(self):
        old = self.make_linear(count=1)
        self.assertEqual(self.design.timeline.timelineGroups.count, 0)
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (1 copy)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.count, 3)
        self.assertTrue(old[0]._deleted)

    def test_clearing_the_copies_field_makes_a_new_pattern_next_to_the_old_one(self):
        old = self.make_linear(count=3)
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        cmd.commandInputs.itemById(C.ID_EXISTING).clearSelection()    # the user empties the highlight
        self.changed(cmd, C.ID_EXISTING)
        self.assertFalse(dialog.editing)
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name, 'New pattern')
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.count, 6)
        self.assertFalse(any(f._deleted for f in old))
        self.assertEqual(self.design.timeline.timelineGroups.count, 2)
        ids = {f.attributes.itemByName('FormulaPattern', 'pattern').value for f in feats.created}
        self.assertEqual(len(ids), 2, 'a new pattern gets its own id')

    def test_a_formula_error_while_editing_leaves_the_old_pattern_alone(self):
        old = self.make_linear(count=3)
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.set_text(cmd, C.ID_FORMULA, 'a * (')
        self.assertFalse(self.preview(cmd).isValidResult)
        self.assertFalse(any(f._deleted for f in old))
        cmd.execute.fire(adsk.core.CommandEventArgs(cmd))
        self.assertFalse(any(f._deleted for f in old))

    def test_sketch_pattern_is_loaded_from_a_copied_curve_and_rebuilt_without_orphans(self):
        sketch = adsk.fusion.Sketch('Fretboard')
        fret, axis_line = sketch.line('fret slot'), sketch.line('centre line')
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, fret)
        self.select(cmd, C.ID_DIRECTION, axis_line)
        self.choose(cmd, C.ID_PRESET, 'Guitar frets (12-TET)')
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        old = list(sketch.geometricConstraints.created)
        old_copies = [e for c in old for e in c.createdEntities]
        self.assertEqual(len(old_copies), 3)

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Fretboard sketch pattern (3 copies)')
        self.assertTrue(dialog.editing)
        self.assertEqual(self.highlighted(cmd), old_copies, 'the copied curves are highlighted')
        self.assertEqual(dialog.object_type, 'Sketch curves')
        self.assertEqual(dialog.preset_id, 'frets')
        self.assertEqual(dialog.count, 3)
        self.assertIs(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity, fret)
        self.assertIs(cmd.commandInputs.itemById(C.ID_DIRECTION).selection(0).entity, axis_line)

        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = sketch.geometricConstraints.created
        self.assertEqual(len(made), 2)
        self.assertTrue(all(c._deleted for c in old))
        self.assertEqual(sketch.deleted, old_copies, 'the old copies are deleted, not left behind')
        self.assertFalse(fret._deleted)
        self.assertEqual(made[0].input.entities, [fret])
        self.assertIs(made[0].input.directionOneEntity, axis_line)

    def test_circular_sketch_pattern_loads_its_centre_point(self):
        sketch = adsk.fusion.Sketch('Wheel')
        spoke, hub = sketch.line('spoke'), sketch.point('hub')
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, spoke)
        self.select(cmd, C.ID_AXIS, hub)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Wheel sketch pattern (10 copies)')
        self.assertTrue(dialog.editing)
        self.assertTrue(dialog.circular)
        self.assertIs(cmd.commandInputs.itemById(C.ID_AXIS).selection(0).entity, hub)
        # The original carries the constraints too, so clicking it picks the same pattern.
        self.pick(cmd, 'New pattern')
        self.pick(cmd, 'Wheel sketch pattern (10 copies)')
        self.click_copy(cmd, spoke)
        self.assertTrue(dialog.editing)
        self.assertEqual(len(self.highlighted(cmd)), 10)

    def test_the_copies_input_accepts_features_and_sketch_curves_without_limit(self):
        cmd, dialog = self.open_dialog()
        existing = cmd.commandInputs.itemById(C.ID_EXISTING)
        # 1.2 added bodies and occurrences (copies made by the transform engine) to the filters.
        self.assertEqual(existing.selectionFilters, ['Features', 'SketchCurves', 'SketchPoints', 'Bodies', 'Occurrences'])
        self.assertEqual(existing.getSelectionLimits()[1:], (0, 0))
        self.assertEqual(existing.selectionCount, 0)


if __name__ == '__main__':
    unittest.main()
