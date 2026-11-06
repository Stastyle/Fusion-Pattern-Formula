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
        sel = cmd.commandInputs.itemById(input_id)
        for e in entities:
            sel.addSelection(e if isinstance(e, adsk.core.Base) else adsk.fusion.Entity(e))
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
        self.assertEqual(dialog.formula_text, 'a * (1 - 1 / 2^(i / 12))')
        self.assertAlmostEqual(dialog.a, 648.0)
        self.assertEqual(dialog.mode, C.engine.MODE_POSITION)
        self.assertIn('scale length', cmd.commandInputs.itemById(C.ID_NOTES).text)
        cmd.commandInputs.itemById(C.ID_COUNT).value = 12
        self.changed(cmd, C.ID_COUNT)
        self.assertIn('i = 12: 324 mm', self.preview_text(cmd))

    def test_a_accepts_units_in_the_expression(self):
        cmd, dialog = self.open_dialog()
        self.set_expression(cmd, C.ID_A_LEN, '25.5 in')
        self.assertAlmostEqual(dialog.a, 647.7)

    def test_editing_the_formula_switches_preset_to_custom(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Fibonacci gaps')
        self.assertEqual(dialog.preset_id, 'fibonacci')
        self.set_text(cmd, C.ID_FORMULA, 'a * fib(i) + 1')
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
        self.set_text(cmd, C.ID_FORMULA, 'a * i')
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
        self.set_text(cmd, C.ID_FORMULA, 'scale * (1 - 1 / 2^(i/12))')
        self.assertIn('i = 12: 324 mm', self.preview_text(cmd))


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
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, 'rib')
        self.select(cmd, C.ID_DIRECTION, 'edge')
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
        self.assertEqual(self.saved[0]['formula'], 'a * b^(i - 1)')
        self.assertEqual(self.saved[0]['b'], '1.2')

    def test_settings_are_restored_next_time_without_reapplying_the_preset(self):
        stored = {
            'pattern_type': 'circular', 'object_type': 'Features', 'count': 7, 'preset': 'geometric',
            'mode': 'spacing', 'formula': 'a * b^(i - 1) + c', 'a_angle': '12 deg', 'b': '1.1', 'c': '0.5',
            'flip': True, 'live': False, 'compute': 2, 'group_name': 'Fins',
        }
        with mock.patch.object(command, 'load_settings', return_value=stored):
            cmd, dialog = self.open_dialog()
        self.assertTrue(dialog.circular)
        self.assertEqual(dialog.object_type, 'Features')
        self.assertEqual(dialog.count, 7)
        self.assertEqual(dialog.preset_id, 'geometric')
        self.assertEqual(dialog.mode, 'spacing')
        self.assertEqual(dialog.formula_text, 'a * b^(i - 1) + c')
        self.assertAlmostEqual(dialog.a, 12.0)
        self.assertAlmostEqual(dialog.b, 1.1)
        self.assertAlmostEqual(dialog.c, 0.5)
        self.assertTrue(dialog.flip)
        self.assertFalse(dialog.live)
        self.assertEqual(dialog.compute_option, adsk.fusion.PatternComputeOptions.AdjustPatternCompute)
        self.assertEqual(dialog.group_name, 'Fins')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selectionFilters, ['Features'])

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
    """Click any copy of a saved Formula Pattern in the "Saved pattern" input: the dialog loads
    its formula, values, objects and direction, and OK replaces the pattern in place."""

    def finish(self, cmd):
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))

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

    def load(self, cmd, entity):
        self.select(cmd, C.ID_EXISTING, entity)

    # ---- loading ----

    def test_every_copy_carries_the_pattern_id_the_edit_path_looks_for(self):
        feats = self.make_linear()
        ids = {f.attributes.itemByName('FormulaPattern', 'pattern').value for f in feats}
        self.assertEqual(len(ids), 1)
        self.assertTrue(next(iter(ids)))

    def test_picking_a_copy_loads_its_settings_objects_and_direction(self):
        feats = self.make_linear(count=4, group='Frets')
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')          # the dialog must switch back to linear
        self.load(cmd, feats[2])
        self.assertFalse(dialog.circular)
        self.assertEqual(dialog.object_type, 'Bodies')
        self.assertEqual(dialog.preset_id, 'frets')
        self.assertEqual(dialog.count, 4)
        self.assertEqual(dialog.group_name, 'Frets')
        objects = cmd.commandInputs.itemById(C.ID_OBJECTS)
        self.assertEqual([objects.selection(k).entity.name for k in range(objects.selectionCount)], ['slot'])
        direction = cmd.commandInputs.itemById(C.ID_DIRECTION)
        self.assertEqual(direction.selection(0).entity.name, 'neck edge')
        self.assertTrue(dialog.editing)
        self.assertIn('Editing', self.preview_text(cmd))
        self.assertIn('Frets', self.preview_text(cmd))
        self.assertTrue(self.validate(cmd))

    def test_loading_a_circular_pattern_switches_the_type_and_fills_the_axis(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, 'rib')
        self.select(cmd, C.ID_AXIS, 'cylinder')
        self.choose(cmd, C.ID_PRESET, 'Golden angle (phyllotaxis)')
        self.preview(cmd)
        self.finish(cmd)
        copy = self.design.rootComponent.features.circularPatternFeatures.item(1)

        cmd, dialog = self.open_dialog()
        self.load(cmd, copy)
        self.assertTrue(dialog.circular)
        self.assertEqual(dialog.preset_id, 'golden_angle')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selection(0).entity.name, 'rib')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_AXIS).selection(0).entity.name, 'cylinder')
        self.assertTrue(self.validate(cmd))

    def test_a_feature_that_is_not_a_formula_pattern_copy_is_refused(self):
        cmd, dialog = self.open_dialog()
        self.ready_linear(cmd)
        self.load(cmd, self.design.rootComponent.features.otherFeatures.add('Fillet1'))
        self.assertFalse(dialog.editing)
        self.assertFalse(self.validate(cmd), 'OK must stay off until the stray selection is cleared')
        self.assertIn('not a copy made by Formula Pattern', self.preview_text(cmd))
        cmd.commandInputs.itemById(C.ID_EXISTING).clearSelection()
        self.changed(cmd, C.ID_EXISTING)
        self.assertTrue(self.validate(cmd))
        self.assertNotIn('not a copy', self.preview_text(cmd))

    def test_a_copy_made_by_an_older_version_says_so(self):
        old = self.design.rootComponent.features.otherFeatures.add('Formula Pattern 1/3')
        old.attributes.add('FormulaPattern', 'settings', '{}')
        old.attributes.add('FormulaPattern', 'copy', '1')
        cmd, dialog = self.open_dialog()
        self.load(cmd, old)
        self.assertFalse(dialog.editing)
        self.assertIn('older version', self.preview_text(cmd))

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
        self.load(cmd, old[1])
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
        self.load(cmd, old[0])
        cmd.commandInputs.itemById(C.ID_COUNT).value = 2
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(timeline.markerPosition, 2)
        self.assertEqual(timeline.names()[-1], 'Fillet1')

    def test_a_single_copy_pattern_outside_any_group_is_replaced_too(self):
        old = self.make_linear(count=1)
        self.assertEqual(self.design.timeline.timelineGroups.count, 0)
        cmd, dialog = self.open_dialog()
        self.load(cmd, old[0])
        cmd.commandInputs.itemById(C.ID_COUNT).value = 3
        self.changed(cmd, C.ID_COUNT)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        feats = self.design.rootComponent.features.rectangularPatternFeatures
        self.assertEqual(feats.count, 3)
        self.assertTrue(old[0]._deleted)

    def test_clearing_the_saved_pattern_makes_a_new_pattern_next_to_the_old_one(self):
        old = self.make_linear(count=3)
        cmd, dialog = self.open_dialog()
        self.load(cmd, old[0])
        cmd.commandInputs.itemById(C.ID_EXISTING).clearSelection()
        self.changed(cmd, C.ID_EXISTING)
        self.assertFalse(dialog.editing)
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
        self.load(cmd, old[0])
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
        self.load(cmd, old_copies[1])                 # the user clicks the second copy
        self.assertTrue(dialog.editing)
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
        self.load(cmd, spoke)                          # the original carries the constraints too
        self.assertTrue(dialog.editing)
        self.assertTrue(dialog.circular)
        self.assertIs(cmd.commandInputs.itemById(C.ID_AXIS).selection(0).entity, hub)

    def test_the_saved_pattern_input_accepts_features_and_sketch_curves(self):
        cmd, dialog = self.open_dialog()
        existing = cmd.commandInputs.itemById(C.ID_EXISTING)
        self.assertEqual(existing.selectionFilters, ['Features', 'SketchCurves', 'SketchPoints'])
        self.assertEqual(existing.getSelectionLimits()[1:], (0, 1))
        self.assertEqual(existing.selectionCount, 0)


if __name__ == '__main__':
    unittest.main()
