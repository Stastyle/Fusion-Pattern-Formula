"""Release 1.2 of FormulaPattern/command.py against the fake adsk package: the Path pattern
type, the Profiles object type, the "Vary each copy" group, the transform build engine and
editing the patterns it makes. Same harness as test_command.py.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import json
import math
import unittest
from unittest import mock

from test_command import FusionHarness, adsk, command as _command  # noqa: F401  (sets up sys.path)

import test_command

C = None   # the live command module; bound per test by VaryHarness.setUp (run() reloads it)


def xyz(point):
    return (point.x, point.y, point.z)


class VaryHarness(FusionHarness):
    def setUp(self):
        global C
        super().setUp()
        C = test_command.command
        self.root = self.design.rootComponent

    # ---- helpers ----

    def edge(self, name, start, end):
        return adsk.fusion.Entity(name, 'BRepEdge', start=start, end=end)

    def axis(self, name, start, end):
        return adsk.fusion.Entity(name, 'ConstructionAxis', start=start, end=end)

    def vertex(self, name, at):
        return adsk.fusion.Entity(name, 'BRepVertex', start=at)

    def set_count(self, cmd, n):
        cmd.commandInputs.itemById(C.ID_COUNT).value = n
        self.changed(cmd, C.ID_COUNT)

    def visible(self, cmd, input_id):
        return cmd.commandInputs.itemById(input_id).isVisible

    def limits(self, cmd, input_id):
        return cmd.commandInputs.itemById(input_id).getSelectionLimits()[1:]

    def names(self, cmd, input_id):
        return [i.name for i in cmd.commandInputs.itemById(input_id).listItems]

    def assertXYZ(self, actual, expected, msg=None, places=9):
        actual = xyz(actual) if hasattr(actual, 'x') else tuple(actual)
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=places, msg=f'{actual} != {expected}' + (f': {msg}' if msg else ''))
        self.assertEqual(len(actual), len(expected))

    def finish(self, cmd):
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CompletedTerminationReason))

    def pick(self, cmd, label):
        self.choose(cmd, C.ID_PATTERN_LIST, label)

    def list_names(self, cmd):
        return self.names(cmd, C.ID_PATTERN_LIST)

    def highlighted(self, cmd):
        sel = cmd.commandInputs.itemById(C.ID_EXISTING)
        return [sel.selection(k).entity for k in range(sel.selectionCount)]

    def selected(self, cmd, input_id):
        sel = cmd.commandInputs.itemById(input_id)
        return [sel.selection(k).entity for k in range(sel.selectionCount)]

    def timeline_entities(self):
        """Every entity in the timeline, groups flattened, in order."""
        out = []
        for row in self.design.timeline._visible():
            if isinstance(row, adsk.fusion.TimelineGroup):
                out += [m.entity for m in row]
            else:
                out.append(row.entity)
        return out


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

class PathType(VaryHarness):
    def test_path_shows_direction_2_coordinates_and_the_second_formula(self):
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.names(cmd, C.ID_TYPE), ['Linear', 'Circular', 'Path'])
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.assertEqual(dialog.pattern_type, 'path')
        self.assertTrue(dialog.path)
        self.assertFalse(dialog.circular)
        self.assertTrue(self.visible(cmd, C.ID_DIRECTION))
        self.assertEqual(self.limits(cmd, C.ID_DIRECTION), (1, 1))
        self.assertIn('X direction', cmd.commandInputs.itemById(C.ID_DIRECTION).commandPrompt)
        self.assertTrue(self.visible(cmd, C.ID_DIRECTION2))
        self.assertEqual(self.limits(cmd, C.ID_DIRECTION2), (1, 1), 'required')
        self.assertEqual(cmd.commandInputs.itemById(C.ID_DIRECTION2).selectionFilters, list(C.DIRECTION_FILTERS))
        self.assertFalse(self.visible(cmd, C.ID_AXIS))
        self.assertTrue(self.visible(cmd, C.ID_COORDS))
        self.assertEqual(self.names(cmd, C.ID_COORDS), ['X / Y', 'Radius / Angle'])
        self.assertFalse(dialog.polar)
        self.assertFalse(self.visible(cmd, C.ID_MODE), 'Formula gives has no meaning for a path')
        self.assertTrue(self.visible(cmd, C.ID_FORMULA2))
        self.assertEqual(dialog.formula2_text, '0')
        self.assertTrue(self.visible(cmd, C.ID_A_LEN), 'a is a length')
        self.assertFalse(self.visible(cmd, C.ID_A_ANG))
        self.assertEqual(self.names(cmd, C.ID_PRESET), [p.name for p in C.engine.presets_for('path')])
        self.assertIn('Sunflower (phyllotaxis)', self.names(cmd, C.ID_PRESET))
        self.assertNotIn('Guitar frets (12-TET)', self.names(cmd, C.ID_PRESET))
        self.assertEqual(dialog.preset_id, 'custom')
        notes = cmd.commandInputs.itemById(C.ID_NOTES).text
        self.assertIn('Formula = X offset', notes)
        self.assertIn('Second formula = Y offset', notes)

    def test_in_a_sketch_direction_1_is_optional_and_direction_2_hidden(self):
        sketch = adsk.fusion.Sketch('Holes')
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.assertTrue(dialog.sketch_mode)
        self.assertTrue(self.visible(cmd, C.ID_DIRECTION))
        self.assertEqual(self.limits(cmd, C.ID_DIRECTION), (0, 1))
        self.assertIn('sketch X axis', cmd.commandInputs.itemById(C.ID_DIRECTION).commandPrompt)
        self.assertFalse(self.visible(cmd, C.ID_DIRECTION2))
        self.assertEqual(self.limits(cmd, C.ID_DIRECTION2), (0, 1), 'a hidden input must not demand a selection')
        self.select(cmd, C.ID_OBJECTS, sketch.circle('hole'))
        self.assertTrue(self.validate(cmd), 'no direction needed in a sketch')

    def test_a_path_preset_fills_both_formulas_and_the_coordinates(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.choose(cmd, C.ID_PRESET, 'Archimedean spiral')
        self.assertEqual(dialog.preset_id, 'spiral')
        self.assertEqual(dialog.formula_text, 'a * t')
        self.assertEqual(dialog.formula2_text, 'b * i')
        self.assertTrue(dialog.polar)
        self.assertAlmostEqual(dialog.a, 100.0)
        self.assertAlmostEqual(dialog.b, 30.0)
        notes = cmd.commandInputs.itemById(C.ID_NOTES).text
        self.assertIn('Formula = radius', notes)
        self.assertIn('angle in degrees', notes)
        self.assertIn('radius of the last copy', notes)
        self.assertIn('i = 1: (', self.preview_text(cmd))
        self.choose(cmd, C.ID_PRESET, 'Sine wave')
        self.assertEqual(dialog.formula_text, 'a * i')
        self.assertEqual(dialog.formula2_text, 'b * sin(2 * pi * t * c)')
        self.assertFalse(dialog.polar)
        self.assertAlmostEqual(dialog.c, 1.0)

    def test_editing_the_second_formula_or_the_coordinates_switches_to_custom(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.choose(cmd, C.ID_PRESET, 'Sine wave')
        self.set_text(cmd, C.ID_FORMULA2, 'b * cos(2 * pi * t * c)')
        self.assertEqual(dialog.preset_id, 'custom')
        self.assertIn('Write any formula', cmd.commandInputs.itemById(C.ID_NOTES).text)
        self.choose(cmd, C.ID_PRESET, 'Sine wave')
        self.assertEqual(dialog.preset_id, 'sine')
        self.choose(cmd, C.ID_COORDS, 'Radius / Angle')
        self.assertEqual(dialog.preset_id, 'custom')
        self.assertTrue(dialog.polar)
        self.assertIn('Formula = radius', cmd.commandInputs.itemById(C.ID_NOTES).text)
        self.choose(cmd, C.ID_PRESET, 'Sine wave')
        self.set_text(cmd, C.ID_FORMULA2, ' b * sin(2 * pi * t * c) ')    # whitespace is not an edit
        self.assertEqual(dialog.preset_id, 'sine')

    def test_switching_between_all_three_types_in_any_order_keeps_the_dialog_consistent(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_PRESET, 'Growing gaps (geometric)')

        def check(kind):
            circular, path = kind == 'Circular', kind == 'Path'
            self.assertEqual(self.visible(cmd, C.ID_DIRECTION), not circular, kind)
            self.assertEqual(self.visible(cmd, C.ID_DIRECTION2), path, kind)
            self.assertEqual(self.limits(cmd, C.ID_DIRECTION2), ((1, 1) if path else (0, 1)), kind)
            self.assertEqual(self.visible(cmd, C.ID_AXIS), circular, kind)
            self.assertEqual(self.limits(cmd, C.ID_AXIS), ((1, 1) if circular else (0, 1)), kind)
            self.assertEqual(self.visible(cmd, C.ID_COORDS), path, kind)
            self.assertEqual(self.visible(cmd, C.ID_FORMULA2), path, kind)
            self.assertEqual(self.visible(cmd, C.ID_MODE), not path, kind)
            self.assertEqual(self.visible(cmd, C.ID_A_LEN), not circular, kind)
            self.assertEqual(self.visible(cmd, C.ID_A_ANG), circular, kind)
            self.assertEqual(self.names(cmd, C.ID_PRESET),
                             [p.name for p in C.engine.presets_for(kind.lower())], kind)
            if circular:
                self.assertEqual(self.names(cmd, C.ID_MODE), ['Angle from original', 'Angle step from previous'])
            else:
                self.assertEqual(self.names(cmd, C.ID_MODE), ['Distance from original', 'Gap to previous'])
            self.assertEqual(cmd.commandInputs.itemById(C.ID_DIRECTION).selectionFilters, list(C.DIRECTION_FILTERS))
            self.assertNotIn('Traceback', self.preview_text(cmd))

        for kind in ('Path', 'Circular', 'Linear', 'Path', 'Linear', 'Circular', 'Path', 'Circular'):
            self.choose(cmd, C.ID_TYPE, kind)
            check(kind)
            if kind == 'Path':
                self.assertEqual(dialog.preset_id, 'custom', 'a linear preset is not a path preset')
        self.choose(cmd, C.ID_TYPE, 'Linear')
        self.assertEqual(dialog.preset_id, 'custom', 'once custom, stays custom')
        self.assertEqual(dialog.mode, C.engine.MODE_SPACING, 'the mode survives the round trip')

    def test_preview_shows_path_points_and_what_varies(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.set_count(cmd, 3)
        text = self.preview_text(cmd)
        self.assertIn('3 copies', text)
        self.assertIn('farthest copy 30 mm', text)
        self.assertIn('i = 1: (10, 0) mm', text)
        self.assertIn('i = 3: (30, 0) mm', text)
        self.set_text(cmd, C.ID_ROTATION, '5 * i')
        self.assertIn('i = 2: (20, 0) mm · turn 10°', self.preview_text(cmd))
        self.set_text(cmd, C.ID_SIZE, '1 + i')
        self.assertIn('· turn 10° · ×3', self.preview_text(cmd))
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.assertIn('· ⌀3 mm', self.preview_text(cmd))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.set_text(cmd, C.ID_DEPTH, 'a * i')
        self.assertIn('· depth 20 mm', self.preview_text(cmd))

    def test_spec_carries_the_new_fields(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.choose(cmd, C.ID_PRESET, 'Logarithmic spiral')
        self.set_text(cmd, C.ID_ROTATION, ' b * i ')
        self.set_text(cmd, C.ID_SIZE, '1 + 0.1 * i')
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.set_text(cmd, C.ID_DEPTH, 'a * ((i * i) % 7) / 7')
        spec = dialog.spec()
        self.assertTrue(spec.path)
        self.assertFalse(spec.circular)
        self.assertEqual(spec.formula, 'a * b^i')
        self.assertEqual(spec.formula2, 'c * i')
        self.assertTrue(spec.polar)
        self.assertEqual(spec.rotation, 'b * i')
        self.assertEqual(spec.size, '1 + 0.1 * i')
        self.assertEqual(spec.size_kind, C.engine.SIZE_DIAMETER)
        self.assertEqual(spec.depth, 'a * ((i * i) % 7) / 7')
        self.assertTrue(dialog.transform_mode)
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Bodies')
        self.assertEqual(dialog.spec().depth, '', 'depth only counts for profiles')
        self.choose(cmd, C.ID_TYPE, 'Linear')
        self.assertFalse(dialog.spec().path)
        self.assertTrue(dialog.transform_mode, 'rotation and size still vary')
        self.set_text(cmd, C.ID_ROTATION, '')
        self.set_text(cmd, C.ID_SIZE, '')
        self.assertFalse(dialog.transform_mode, 'a plain linear pattern uses the native features')


class ProfilesType(VaryHarness):
    def test_profiles_object_type_shows_depth_operation_and_target_for_join_or_cut(self):
        cmd, dialog = self.open_dialog()
        self.assertFalse(self.visible(cmd, C.ID_DEPTH))
        self.assertFalse(self.visible(cmd, C.ID_OPERATION))
        self.assertFalse(self.visible(cmd, C.ID_TARGET))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.assertEqual(dialog.object_type, 'Profiles')
        self.assertTrue(dialog.profiles_mode)
        self.assertFalse(dialog.sketch_mode)
        self.assertTrue(dialog.transform_mode)
        self.assertEqual(cmd.commandInputs.itemById(C.ID_OBJECTS).selectionFilters, ['Profiles'])
        self.assertTrue(self.visible(cmd, C.ID_ADVANCED_GROUP))
        self.assertEqual(cmd.commandInputs.itemById(C.ID_DIRECTION).selectionFilters, list(C.DIRECTION_FILTERS))
        self.assertTrue(self.visible(cmd, C.ID_DEPTH))
        self.assertTrue(self.visible(cmd, C.ID_OPERATION))
        self.assertEqual(self.names(cmd, C.ID_OPERATION), ['New body', 'Join', 'Cut'])
        self.assertEqual(dialog.operation, 0)
        self.assertFalse(self.visible(cmd, C.ID_TARGET))
        self.choose(cmd, C.ID_OPERATION, 'Cut')
        self.assertEqual(dialog.operation, 2)
        self.assertTrue(self.visible(cmd, C.ID_TARGET))
        self.assertEqual(cmd.commandInputs.itemById(C.ID_TARGET).selectionFilters, ['SolidBodies'])
        self.assertEqual(self.limits(cmd, C.ID_TARGET), (0, 1))
        self.choose(cmd, C.ID_OPERATION, 'Join')
        self.assertEqual(dialog.operation, 1)
        self.assertTrue(self.visible(cmd, C.ID_TARGET))
        self.choose(cmd, C.ID_OPERATION, 'New body')
        self.assertFalse(self.visible(cmd, C.ID_TARGET))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Bodies')
        self.assertFalse(self.visible(cmd, C.ID_DEPTH))
        self.assertFalse(self.visible(cmd, C.ID_OPERATION))


class VaryGroup(VaryHarness):
    def test_the_group_follows_the_formula_group_and_starts_collapsed(self):
        cmd, dialog = self.open_dialog()
        ids = [i.id for i in cmd.commandInputs]
        self.assertEqual(ids.index(C.ID_VARY_GROUP), ids.index(C.ID_FORMULA_GROUP) + 1)
        group = cmd.commandInputs.itemById(C.ID_VARY_GROUP)
        self.assertFalse(group.isExpanded)
        self.assertEqual([i.id for i in group.children],
                         [C.ID_ROTATION, C.ID_ROTATION_AXIS, C.ID_SIZE, C.ID_SIZE_KIND, C.ID_SCALE_POINT,
                          C.ID_DEPTH, C.ID_OPERATION, C.ID_TARGET])
        for input_id in (C.ID_ROTATION, C.ID_ROTATION_AXIS, C.ID_SIZE, C.ID_SIZE_KIND, C.ID_SCALE_POINT,
                         C.ID_DEPTH, C.ID_OPERATION, C.ID_TARGET):
            tooltip = cmd.commandInputs.itemById(input_id).tooltip
            self.assertTrue(tooltip, input_id)
        self.assertIn('degrees', cmd.commandInputs.itemById(C.ID_ROTATION).tooltip)
        self.assertIn('mm', cmd.commandInputs.itemById(C.ID_SIZE).tooltip)
        self.assertIn('mm', cmd.commandInputs.itemById(C.ID_DEPTH).tooltip)
        self.assertEqual(dialog.rotation_text, '')
        self.assertEqual(dialog.size_text, '')
        self.assertEqual(dialog.size_kind, C.engine.SIZE_SCALE)
        self.assertEqual(self.names(cmd, C.ID_SIZE_KIND), ['Scale factor', 'Diameter (circles)'])

    def test_solid_objects_get_a_rotation_axis_and_a_scale_point(self):
        cmd, dialog = self.open_dialog()
        axis = cmd.commandInputs.itemById(C.ID_ROTATION_AXIS)
        self.assertTrue(axis.isVisible)
        self.assertEqual(axis.selectionFilters, list(C.DIRECTION_FILTERS))
        self.assertIn('axis', axis.commandPrompt)
        self.assertEqual(self.limits(cmd, C.ID_ROTATION_AXIS), (0, 1))
        point = cmd.commandInputs.itemById(C.ID_SCALE_POINT)
        self.assertTrue(point.isVisible)
        self.assertEqual(point.selectionFilters, ['Vertices', 'SketchPoints', 'ConstructionPoints'])
        self.assertEqual(self.limits(cmd, C.ID_SCALE_POINT), (0, 1))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        self.assertTrue(point.isVisible, 'solid object types show it (Size itself is refused for components)')

    def test_sketch_curves_get_a_rotation_centre_and_no_scale_point(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_ROTATION_AXIS, 'edge')
        self.select(cmd, C.ID_SCALE_POINT, self.vertex('corner', (0, 0, 0)))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Sketch curves')
        axis = cmd.commandInputs.itemById(C.ID_ROTATION_AXIS)
        self.assertTrue(axis.isVisible)
        self.assertEqual(axis.selectionFilters, ['SketchPoints'])
        self.assertIn('sketch point', axis.commandPrompt)
        self.assertEqual(axis.selectionCount, 0, 'a model edge cannot be a sketch rotation centre')
        self.assertEqual(self.limits(cmd, C.ID_ROTATION_AXIS), (0, 1))
        point = cmd.commandInputs.itemById(C.ID_SCALE_POINT)
        self.assertFalse(point.isVisible)
        self.assertEqual(point.selectionCount, 0)
        self.assertEqual(self.limits(cmd, C.ID_SCALE_POINT), (0, 1))
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Bodies')
        self.assertEqual(axis.selectionFilters, list(C.DIRECTION_FILTERS))
        self.assertTrue(point.isVisible)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class ValidationMessages(VaryHarness):
    def setUp(self):
        super().setUp()
        self.block = self.root.body('Block')
        self.x_edge = self.edge('x edge', (0, 0, 0), (1, 0, 0))
        self.y_edge = self.edge('y edge', (0, 0, 0), (0, 1, 0))

    def ready_bodies(self, cmd):
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)

    def assertProblem(self, cmd, message):
        self.assertFalse(self.validate(cmd), message)
        self.assertIn(message, self.preview_text(cmd))

    def assertFine(self, cmd):
        self.assertTrue(self.validate(cmd), self.preview_text(cmd))
        self.assertNotIn('✖', self.preview_text(cmd))

    def test_path_needs_direction_2_and_refuses_a_parallel_one(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.ready_bodies(cmd)
        self.assertProblem(cmd, 'Path needs Direction 2')
        self.select(cmd, C.ID_DIRECTION2, self.edge('other x', (0, 0, 0), (-2, 0, 0)))
        self.assertProblem(cmd, 'Direction 2 must not be parallel to Direction 1')
        cmd.commandInputs.itemById(C.ID_DIRECTION2).clearSelection()
        self.select(cmd, C.ID_DIRECTION2, self.edge('tilted', (0, 0, 0), (1, 1, 0)))
        self.assertFine(cmd)

    def test_path_rotation_and_size_need_bodies_components_sketch_curves_or_profiles(self):
        message = 'Path, Rotation and Size need bodies, components, sketch curves or profiles'
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Faces')
        self.select(cmd, C.ID_OBJECTS, adsk.fusion.Entity('top', 'BRepFace'))
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.assertFine(cmd)
        self.set_text(cmd, C.ID_ROTATION, 'b * i')
        self.assertProblem(cmd, message)
        self.set_text(cmd, C.ID_ROTATION, '')
        self.set_text(cmd, C.ID_SIZE, '1 + i')
        self.assertProblem(cmd, message)
        self.set_text(cmd, C.ID_SIZE, '')
        self.assertFine(cmd)
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Features')
        self.select(cmd, C.ID_OBJECTS, adsk.fusion.Entity('slot cut', 'ExtrudeFeature'))
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.assertProblem(cmd, message)

    def test_rotation_needs_an_axis_outside_a_sketch(self):
        cmd, dialog = self.open_dialog()
        self.ready_bodies(cmd)
        self.set_text(cmd, C.ID_ROTATION, '10 * i')
        self.assertProblem(cmd, 'Rotation needs a rotation axis')
        self.select(cmd, C.ID_ROTATION_AXIS, self.axis('z', (0, 0, 0), (0, 0, 1)))
        self.assertFine(cmd)
        sketch = adsk.fusion.Sketch('Holes')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Sketch curves')
        self.select(cmd, C.ID_OBJECTS, sketch.line('slot'))
        self.assertFine(cmd)     # the centre of the selection is the default rotation centre

    def test_size_does_not_apply_to_components(self):
        wheel = self.design.add_component('Wheel', 'Wheel:1')
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        self.select(cmd, C.ID_OBJECTS, self.root.occurrences.item(0))
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.assertFine(cmd)
        self.set_text(cmd, C.ID_SIZE, '1 + 0.1 * i')
        self.assertProblem(cmd, 'Size does not apply to components')

    def test_size_needs_a_point_to_scale_bodies_and_profiles_about(self):
        cmd, dialog = self.open_dialog()
        self.ready_bodies(cmd)
        self.set_text(cmd, C.ID_SIZE, '1 + 0.1 * i')
        self.assertProblem(cmd, 'Size needs a point to scale about')
        self.select(cmd, C.ID_SCALE_POINT, self.vertex('corner', (0, 0, 0)))
        self.assertFine(cmd)
        sketch = adsk.fusion.Sketch('Wells', self.root)
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.select(cmd, C.ID_OBJECTS, sketch.profile('well'))
        self.set_text(cmd, C.ID_DEPTH, 'a')
        self.assertFine(cmd)
        cmd.commandInputs.itemById(C.ID_SCALE_POINT).clearSelection()
        self.changed(cmd, C.ID_SCALE_POINT)
        self.assertProblem(cmd, 'Size needs a point to scale about')

    def test_diameter_needs_sketch_circles(self):
        cmd, dialog = self.open_dialog()
        self.ready_bodies(cmd)
        self.select(cmd, C.ID_SCALE_POINT, self.vertex('corner', (0, 0, 0)))
        self.set_text(cmd, C.ID_SIZE, 'a + i')
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.assertProblem(cmd, 'Diameter needs sketch circles')
        sketch = adsk.fusion.Sketch('Holes')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Sketch curves')
        self.select(cmd, C.ID_OBJECTS, sketch.circle('hole'), sketch.line('slot'))
        self.assertProblem(cmd, 'Diameter needs sketch circles')
        cmd.commandInputs.itemById(C.ID_OBJECTS).clearSelection()
        self.select(cmd, C.ID_OBJECTS, sketch.circle('hole'), sketch.circle('hole 2', centre=(1, 1)))
        self.assertFine(cmd)
        self.assertIn('⌀', self.preview_text(cmd))

    def test_profiles_need_a_depth_and_join_or_cut_a_target(self):
        sketch = adsk.fusion.Sketch('Wells', self.root)
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.select(cmd, C.ID_OBJECTS, sketch.profile('well'))
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.assertProblem(cmd, 'Profiles need a Depth formula')
        self.set_text(cmd, C.ID_DEPTH, 'a * i')
        self.assertFine(cmd)
        self.choose(cmd, C.ID_OPERATION, 'Cut')
        self.assertProblem(cmd, 'Join and Cut need a Target body')
        self.select(cmd, C.ID_TARGET, self.block)
        self.assertFine(cmd)
        self.choose(cmd, C.ID_OPERATION, 'New body')
        self.assertFine(cmd)

    def test_engine_errors_for_the_new_formulas_block_ok(self):
        sketch = adsk.fusion.Sketch('Wells', self.root)
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.select(cmd, C.ID_OBJECTS, sketch.profile('well'))
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_DEPTH, 'a * (i - 1)')
        self.assertProblem(cmd, 'Copy 1 has depth 0')
        self.set_text(cmd, C.ID_DEPTH, 'a * i')
        self.set_text(cmd, C.ID_ROTATION, 'b *')
        self.assertFalse(self.validate(cmd))
        self.assertIn('Rotation', self.preview_text(cmd))
        self.set_text(cmd, C.ID_ROTATION, '')
        self.select(cmd, C.ID_SCALE_POINT, self.vertex('corner', (0, 0, 0)))
        self.set_text(cmd, C.ID_SIZE, 'i - 1')
        self.assertProblem(cmd, 'Copy 1: size must be greater than 0')
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.set_text(cmd, C.ID_SIZE, '')
        self.set_text(cmd, C.ID_FORMULA2, '')
        self.assertProblem(cmd, 'The Y formula is empty')
        self.set_text(cmd, C.ID_FORMULA2, '0')
        self.set_text(cmd, C.ID_FORMULA, '0')
        self.assertProblem(cmd, 'Copy 1 lands on the original')


# ---------------------------------------------------------------------------
# Building with the transform engine
# ---------------------------------------------------------------------------

class BuildingBodies(VaryHarness):
    def setUp(self):
        super().setUp()
        self.block = self.root.body('Block')
        self.x_edge = self.edge('x edge', (0, 0, 0), (1, 0, 0))
        self.y_edge = self.edge('y edge', (0, 0, 0), (0, 1, 0))

    def new_bodies(self):
        return [b for b in self.root.bRepBodies if b is not self.block]

    def test_copied_bodies_are_named_after_the_original_with_their_copy_number(self):
        # Fusion's own names for pasted bodies come out as 'Peg (1)', 'Peg (1) (1)', 'Peg (2)' …
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_count(cmd, 3)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual([b.name for b in self.new_bodies()], ['Block (1)', 'Block (2)', 'Block (3)'])

    def test_path_pattern_copies_the_body_and_moves_each_copy_along_both_directions(self):
        # "X" is the model Y axis here and Direction 2 is tilted: it is made perpendicular to X.
        along_y = self.edge('along y', (3, 3, 0), (3, 5, 0))
        tilted = self.edge('tilted', (0, 0, 0), (1, 1, 0))
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, along_y)
        self.select(cmd, C.ID_DIRECTION2, tilted)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')      # X formula is 'a * i' with a = 10 mm
        self.set_count(cmd, 3)
        args = self.preview(cmd)
        self.assertTrue(args.isValidResult, self.preview_text(cmd))

        bodies = self.new_bodies()
        self.assertEqual(len(bodies), 3)
        # copy i: x_i = 10 i mm along +Y, y_i = 5 i mm along +X (the tilted edge minus its Y part)
        self.assertXYZ(bodies[0].location(), (0.5, 1.0, 0.0))
        self.assertXYZ(bodies[1].location(), (1.0, 2.0, 0.0))
        self.assertXYZ(bodies[2].location(), (1.5, 3.0, 0.0))
        self.assertXYZ(self.block.location(), (0, 0, 0), 'the original stays')
        self.assertEqual(self.root.features.rectangularPatternFeatures.count, 0, 'no native pattern')

        timeline = self.design.timeline
        self.assertEqual(timeline.names(), [['Formula Pattern 1/3', 'Formula Pattern 1/3',
                                             'Formula Pattern 2/3', 'Formula Pattern 2/3',
                                             'Formula Pattern 3/3', 'Formula Pattern 3/3']])
        self.assertEqual(timeline.timelineGroups.item(0).name, 'Formula Pattern')
        rows = self.timeline_entities()
        self.assertEqual([r.objectType.split('::')[-1] for r in rows[:2]], ['CopyPasteBody', 'MoveFeature'])
        copy_feature, move_feature = rows[0], rows[1]
        self.assertEqual(copy_feature.attributes.itemByName('FormulaPattern', 'copy').value, '1')
        pattern_id = copy_feature.attributes.itemByName('FormulaPattern', 'pattern').value
        self.assertTrue(pattern_id)
        self.assertEqual(move_feature.attributes.itemByName('FormulaPattern', 'pattern').value, pattern_id)
        stored = json.loads(copy_feature.attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['pattern_type'], 'path')
        self.assertEqual(stored['formula2'], '5 * i')
        self.assertEqual(stored['coords'], 'xy')
        self.assertEqual(stored['tokens']['objects'], [self.block.entityToken])
        self.assertEqual(stored['tokens']['direction'], along_y.entityToken)
        self.assertEqual(stored['tokens']['direction2'], tilted.entityToken)
        self.assertIsNone(stored['tokens']['axis'])
        self.assertIsNone(stored['tokens']['rotation_axis'])
        for k, body in enumerate(bodies, 1):
            self.assertEqual(body.attributes.itemByName('FormulaPattern', 'member').value, pattern_id)
            self.assertEqual(body.attributes.itemByName('FormulaPattern', 'copy').value, str(k))
            self.assertIsNone(body.attributes.itemByName('FormulaPattern', 'pattern'), 'bodies are members, not copies')

    def test_polar_path_and_flip(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.choose(cmd, C.ID_COORDS, 'Radius / Angle')
        self.set_text(cmd, C.ID_FORMULA, 'a')          # radius 10 mm
        self.set_text(cmd, C.ID_FORMULA2, '90 * i')     # 90°, 180°
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        bodies = self.new_bodies()
        self.assertXYZ(bodies[0].location(), (0.0, 1.0, 0.0))
        self.assertXYZ(bodies[1].location(), (-1.0, 0.0, 0.0))
        cmd.commandInputs.itemById(C.ID_FLIP).value = True
        self.changed(cmd, C.ID_FLIP)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        bodies = self.new_bodies()[-2:]
        self.assertXYZ(bodies[0].location(), (0.0, -1.0, 0.0), 'flip mirrors Y')

    def test_linear_pattern_with_rotation_turns_each_copy_about_the_axis_then_moves_it(self):
        off_axis = self.root.body('Blade', origin=(1, 0, 0))
        z_axis = self.axis('z axis', (0, 0, 0), (0, 0, 1))
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.block, off_axis)     # Block sits on the axis
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_ROTATION, '90 * i')
        self.select(cmd, C.ID_ROTATION_AXIS, z_axis)
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))

        copies = [b for b in self.root.bRepBodies if b not in (self.block, off_axis)]
        self.assertEqual(len(copies), 4)
        # copy 1 (90°): the body on the axis only moves 10 mm along X
        self.assertXYZ(copies[0].location(), (1.0, 0.0, 0.0))
        # the off-axis body is turned (1,0,0) -> (0,1,0) first, then moved
        self.assertXYZ(copies[1].location(), (1.0, 1.0, 0.0))
        # copy 2 (180°): (1,0,0) -> (-1,0,0), then +20 mm
        self.assertXYZ(copies[2].location(), (2.0, 0.0, 0.0))
        self.assertXYZ(copies[3].location(), (1.0, 0.0, 0.0))
        moves = self.root.features.moveFeatures.created
        self.assertEqual(len(moves), 2, 'one Move per copy moves both bodies')
        cells = C.placement.from_matrix3d(moves[0].transform)
        self.assertXYZ(C.placement.apply_vector(cells, (1, 0, 0)), (0, 1, 0), 'rotation part turns X to Y')
        self.assertXYZ(C.placement.apply_vector(cells, (0, 0, 1)), (0, 0, 1))
        self.assertEqual(self.root.features.scaleFeatures.count, 0)
        self.assertEqual(self.root.features.rectangularPatternFeatures.count, 0)
        stored = json.loads(moves[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['rotation'], '90 * i')
        self.assertEqual(stored['tokens']['rotation_axis'], z_axis.entityToken)

    def test_circular_pattern_with_rotation_uses_the_transform_engine(self):
        blade = self.root.body('Blade', origin=(2, 0, 0))
        hub = self.axis('hub', (0, 0, 0), (0, 0, 1))
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, blade)
        self.select(cmd, C.ID_AXIS, hub)
        self.set_expression(cmd, C.ID_A_ANG, '90 deg')
        self.set_text(cmd, C.ID_ROTATION, '45')
        self.select(cmd, C.ID_ROTATION_AXIS, self.axis('blade axis', (2, 0, 0), (2, 0, 1)))
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        copy = [b for b in self.root.bRepBodies if b not in (self.block, blade)][0]
        self.assertXYZ(copy.location(), (0.0, 2.0, 0.0), 'turned about its own axis, then 90° about the hub')
        cells = C.placement.from_matrix3d(self.root.features.moveFeatures.item(0).transform)
        s = math.sqrt(0.5)
        self.assertXYZ(C.placement.apply_vector(cells, (1, 0, 0)), (-s, s, 0), '45° + 90° = 135°')
        self.assertEqual(self.root.features.circularPatternFeatures.count, 0)

    def test_size_scales_the_copy_about_the_point_before_the_move(self):
        corner = self.vertex('corner', (0, 0, 0))
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_SIZE, '1 + i')
        self.select(cmd, C.ID_SCALE_POINT, corner)
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        bodies = self.new_bodies()
        self.assertEqual([b._scale for b in bodies], [2.0, 3.0])
        self.assertXYZ(bodies[0].location(), (1.0, 0.0, 0.0), 'scaled about the point it sits on, then moved')
        self.assertXYZ(bodies[1].location(), (2.0, 0.0, 0.0))
        self.assertEqual(self.block._scale, 1.0)
        rows = self.timeline_entities()
        self.assertEqual([r.objectType.split('::')[-1] for r in rows],
                         ['CopyPasteBody', 'ScaleFeature', 'MoveFeature'] * 2, 'Scale comes before Move')
        scale = self.root.features.scaleFeatures.item(0)
        self.assertIs(scale.point, corner)
        self.assertEqual(scale.name, 'Formula Pattern 1/2')
        self.assertEqual(scale.attributes.itemByName('FormulaPattern', 'copy').value, '1')
        self.assertEqual(self.design.timeline.names(), [['Formula Pattern 1/2'] * 3 + ['Formula Pattern 2/2'] * 3])

    def test_a_copy_fusion_refuses_is_reported_with_its_place(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.root.features.moveFeatures.fail_at = 2
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('could not create copy 2 of 10 at (20, 10) mm', self.preview_text(cmd))
        self.root.features.moveFeatures.fail_at = None
        # (Fusion drops a failed preview's features itself; the fake keeps them, so the hook
        # counts on from what is there.)
        self.root.features.copyPasteBodies.fail_at = self.root.features.copyPasteBodies.count + 1
        self.assertFalse(self.preview(cmd).isValidResult)
        self.assertIn('could not create copy 1 of 10 at (10, 5) mm', self.preview_text(cmd))

    def test_a_round_axis_entity_is_refused_as_a_direction_with_a_readable_message(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, adsk.fusion.Entity('rim', 'BRepFace'))   # no line geometry
        self.set_text(cmd, C.ID_ROTATION, '10 * i')
        self.select(cmd, C.ID_ROTATION_AXIS, self.x_edge)
        args = self.preview(cmd)
        self.assertFalse(args.isValidResult)
        self.assertIn('Direction: BRepFace has no line geometry', self.preview_text(cmd))

    def test_the_timeline_group_uses_the_custom_name_and_one_copy_is_still_grouped(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_ROTATION, '30')
        self.select(cmd, C.ID_ROTATION_AXIS, self.axis('z', (0, 0, 0), (0, 0, 1)))
        self.set_text(cmd, C.ID_GROUP_NAME, 'Louvres')
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(self.design.timeline.names(), [['Louvres 1/1', 'Louvres 1/1']])
        self.assertEqual(self.design.timeline.timelineGroups.item(0).name, 'Louvres')


class BuildingComponents(VaryHarness):
    def test_path_pattern_adds_one_occurrence_per_copy_with_the_offset_on_its_transform(self):
        wheel = self.design.add_component('Wheel', 'Wheel:1')
        original = self.root.occurrences.item(0)
        lifted = adsk.core.Matrix3D.create()
        lifted.translation = adsk.core.Vector3D.create(0, 0, 2)
        original.transform2 = lifted
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, original)
        self.select(cmd, C.ID_DIRECTION, self.edge('x', (0, 0, 0), (1, 0, 0)))
        self.select(cmd, C.ID_DIRECTION2, self.edge('y', (0, 0, 0), (0, 1, 0)))
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))

        self.assertEqual(self.root.occurrences.count, 3)
        made = [self.root.occurrences.item(1), self.root.occurrences.item(2)]
        self.assertEqual([o.name for o in made], ['Wheel:2', 'Wheel:3'])
        self.assertIs(made[0].component, wheel)
        self.assertXYZ(made[0].transform2.translation, (1.0, 0.5, 2.0), 'offset on top of the original transform')
        self.assertXYZ(made[1].transform2.translation, (2.0, 1.0, 2.0))
        self.assertXYZ(original.transform2.translation, (0, 0, 2), 'the original stays')
        self.assertEqual(self.design.timeline.names(), ['Wheel:1', ['Wheel:2', 'Wheel:3']])
        self.assertEqual(self.design.timeline.timelineGroups.item(0).name, 'Formula Pattern')
        pattern_id = made[0].attributes.itemByName('FormulaPattern', 'pattern').value
        self.assertTrue(pattern_id)
        self.assertEqual(made[1].attributes.itemByName('FormulaPattern', 'copy').value, '2')
        stored = json.loads(made[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['tokens']['objects'], [original.entityToken])

    def test_rotated_components(self):
        wheel = self.design.add_component('Wheel', 'Wheel:1')
        original = self.root.occurrences.item(0)
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        self.select(cmd, C.ID_OBJECTS, original)
        self.select(cmd, C.ID_DIRECTION, self.edge('x', (0, 0, 0), (1, 0, 0)))
        self.set_text(cmd, C.ID_ROTATION, '90')
        self.select(cmd, C.ID_ROTATION_AXIS, self.axis('z', (0, 0, 0), (0, 0, 1)))
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = self.root.occurrences.item(1)
        cells = C.placement.from_matrix3d(made.transform2)
        self.assertXYZ(C.placement.apply(cells, (0, 0, 0)), (1.0, 0.0, 0.0))
        self.assertXYZ(C.placement.apply_vector(cells, (1, 0, 0)), (0, 1, 0))


class BuildingProfiles(VaryHarness):
    def setUp(self):
        super().setUp()
        self.sketch = adsk.fusion.Sketch('Wells', self.root)
        self.well = self.sketch.profile('well', centroid=(0, 0, 0))
        # The block sits under the well profile, as a block under a sketch on its top face:
        # a point just above the profile is outside it.
        self.block = self.root.body('Block', origin=(0, 0, -0.5))
        self.x_edge = self.edge('x edge', (0, 0, 0), (1, 0, 0))

    def ready_profiles(self, cmd, depth, count):
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.select(cmd, C.ID_OBJECTS, self.well)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_DEPTH, depth)
        self.set_count(cmd, count)

    def test_each_copy_is_extruded_by_its_own_signed_depth_moved_and_cut_from_the_target(self):
        cmd, dialog = self.open_dialog()
        self.ready_profiles(cmd, 'a * (2 * i - 3)', 3)       # -10, +10, +30 mm
        self.choose(cmd, C.ID_OPERATION, 'Cut')
        self.select(cmd, C.ID_TARGET, self.block)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))

        extrudes = self.root.features.extrudeFeatures.created
        # A positive depth cuts *into* the block, which lies against the profile's normal.
        self.assertEqual([round(e.depth, 9) for e in extrudes], [1.0, -1.0, -3.0], 'signed centimetres')
        self.assertEqual(extrudes[0].extentOne.distance.stringValue, '10 mm', 'abs(depth) in document units')
        self.assertEqual(extrudes[0].operation, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        self.assertEqual(extrudes[0].profile.item(0), self.well)
        tools = [e.bodies.item(0) for e in extrudes]
        self.assertXYZ(tools[0].location(), (1.0, 0.0, 0.0))
        self.assertXYZ(tools[2].location(), (3.0, 0.0, 0.0))

        combines = self.root.features.combineFeatures.created
        self.assertEqual(len(combines), 1)
        combine = combines[0]
        self.assertIs(combine.targetBody, self.block)
        self.assertEqual(list(combine.toolBodies), tools)
        self.assertEqual(combine.operation, adsk.fusion.FeatureOperations.CutFeatureOperation)
        self.assertFalse(combine.isKeepToolBodies)
        self.assertTrue(all(t._deleted for t in tools), 'the tools are consumed')
        self.assertFalse(self.block._deleted)
        self.assertEqual([b.name for b in self.root.bRepBodies], ['Block'])
        self.assertEqual(combine.name, 'Formula Pattern 3/3')
        self.assertEqual(combine.attributes.itemByName('FormulaPattern', 'copy').value, '3')
        pattern_id = extrudes[0].attributes.itemByName('FormulaPattern', 'pattern').value
        self.assertEqual(combine.attributes.itemByName('FormulaPattern', 'pattern').value, pattern_id)
        self.assertEqual(self.design.timeline.names(),
                         [['Formula Pattern 1/3', 'Formula Pattern 1/3', 'Formula Pattern 2/3', 'Formula Pattern 2/3',
                           'Formula Pattern 3/3', 'Formula Pattern 3/3', 'Formula Pattern 3/3']])
        rows = [r.objectType.split('::')[-1] for r in self.timeline_entities()]
        self.assertEqual(rows, ['ExtrudeFeature', 'MoveFeature'] * 3 + ['CombineFeature'])
        stored = json.loads(extrudes[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['object_type'], 'Profiles')
        self.assertEqual(stored['depth'], 'a * (2 * i - 3)')
        self.assertEqual(stored['operation'], 2)
        self.assertEqual(stored['tokens']['target'], self.block.entityToken)
        self.assertEqual(stored['tokens']['objects'], [self.well.entityToken])

    def test_new_body_keeps_every_copy_and_join_uses_a_join_combine(self):
        cmd, dialog = self.open_dialog()
        self.ready_profiles(cmd, 'a', 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(self.root.features.combineFeatures.count, 0)
        self.assertEqual(self.root.bRepBodies.count, 3)
        bodies = [b for b in self.root.bRepBodies if b is not self.block]
        self.assertXYZ(bodies[1].location(), (2.0, 0.0, 0.0))
        self.assertEqual(bodies[0].attributes.itemByName('FormulaPattern', 'copy').value, '1')
        self.assertTrue(bodies[0].attributes.itemByName('FormulaPattern', 'member').value)
        self.choose(cmd, C.ID_OPERATION, 'Join')
        self.select(cmd, C.ID_TARGET, self.block)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        combine = self.root.features.combineFeatures.item(0)
        self.assertEqual(combine.operation, adsk.fusion.FeatureOperations.JoinFeatureOperation)
        self.assertEqual(combine.toolBodies.count, 2)

    def depth_of_first_copy(self, operation, block_origin):
        self.block = self.root.body('Target', origin=block_origin)
        cmd, dialog = self.open_dialog()
        self.ready_profiles(cmd, 'a', 1)
        self.choose(cmd, C.ID_OPERATION, operation)
        if operation != 'New body':
            self.select(cmd, C.ID_TARGET, self.block)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        depth = self.root.features.extrudeFeatures.item(self.root.features.extrudeFeatures.count - 1).depth
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, adsk.core.CommandTerminationReason.CancelledTerminationReason))
        return round(depth, 9)

    def test_cut_goes_into_the_target_and_join_away_from_it_whichever_side_it_is_on(self):
        below, above = (0, 0, -0.5), (0, 0, 0.5)
        self.assertEqual(self.depth_of_first_copy('Cut', below), -1.0, 'target under the profile: cut goes down')
        self.assertEqual(self.depth_of_first_copy('Join', below), 1.0, 'join grows up, away from it')
        self.assertEqual(self.depth_of_first_copy('Cut', above), 1.0, 'target over the profile: cut goes up')
        self.assertEqual(self.depth_of_first_copy('Join', above), -1.0)
        self.assertEqual(self.depth_of_first_copy('New body', below), 1.0, 'new bodies follow the normal')

    def test_a_target_that_cannot_be_probed_keeps_the_profile_normal(self):
        self.block = self.root.body('Target', origin=(0, 0, -0.5))
        self.block.pointContainment = lambda point: (_ for _ in ()).throw(RuntimeError('3 : no'))
        cmd, dialog = self.open_dialog()
        self.ready_profiles(cmd, 'a', 1)
        self.choose(cmd, C.ID_OPERATION, 'Cut')
        self.select(cmd, C.ID_TARGET, self.block)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(round(self.root.features.extrudeFeatures.item(0).depth, 9), 1.0)

    def test_size_scales_the_extruded_copy_before_the_move(self):
        cmd, dialog = self.open_dialog()
        self.ready_profiles(cmd, 'a', 1)
        self.set_text(cmd, C.ID_SIZE, '2')
        self.select(cmd, C.ID_SCALE_POINT, self.vertex('corner', (0, 0, 0)))
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        body = self.root.features.extrudeFeatures.item(0).bodies.item(0)
        self.assertEqual(body._scale, 2.0)
        self.assertXYZ(body.location(), (1.0, 0.0, 0.0))
        rows = [r.objectType.split('::')[-1] for r in self.timeline_entities()]
        self.assertEqual(rows, ['ExtrudeFeature', 'ScaleFeature', 'MoveFeature'])


class BuildingSketchCopies(VaryHarness):
    def setUp(self):
        super().setUp()
        self.sketch = adsk.fusion.Sketch('Holes')
        self.app.activeEditObject = self.sketch
        self.hole = self.sketch.circle('hole', centre=(0, 0), radius=0.2)

    def copies(self):
        return list(self.sketch.copies)

    def test_path_with_diameter_copies_the_circle_and_sets_each_radius(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.hole)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_text(cmd, C.ID_SIZE, 'a + 2 * i')         # 12 mm, 14 mm
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))

        made = self.copies()
        self.assertEqual(len(made), 2)
        self.assertTrue(all(isinstance(c, adsk.fusion.SketchCircle) for c in made))
        self.assertXYZ(made[0].geometry.center, (1.0, 0.5, 0.0), 'sketch X / Y axes without a line')
        self.assertXYZ(made[1].geometry.center, (2.0, 1.0, 0.0))
        self.assertAlmostEqual(made[0].radius, 0.6)
        self.assertAlmostEqual(made[1].radius, 0.7)
        self.assertEqual(self.hole.radius, 0.2, 'the original stays')
        self.assertEqual(self.sketch.geometricConstraints.created, [], 'no pattern constraint')
        self.assertEqual(self.design.timeline.count, 0)
        pattern_id = made[0].attributes.itemByName('FormulaPattern', 'pattern').value
        self.assertTrue(pattern_id)
        self.assertEqual(made[1].attributes.itemByName('FormulaPattern', 'member').value, pattern_id)
        self.assertEqual(made[1].attributes.itemByName('FormulaPattern', 'copy').value, '2')
        stored = json.loads(made[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['size_kind'], 'diameter')
        self.assertEqual(stored['tokens']['objects'], [self.hole.entityToken])
        self.assertIsNone(stored['tokens']['direction'])

    def test_path_along_a_sketch_line_uses_the_line_and_its_perpendicular(self):
        line = self.sketch.line('dir', start=(0, 0), end=(0, 3))      # X of the path is sketch +Y
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.hole)
        self.select(cmd, C.ID_DIRECTION, line)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertXYZ(self.copies()[0].geometry.center, (-0.5, 1.0, 0.0))
        self.assertEqual(self.copies()[0].radius, 0.2)

    def test_scale_factor_scales_each_copy_about_its_moved_centre(self):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.hole)
        self.set_text(cmd, C.ID_SIZE, '1 + i')              # ×2, ×3
        self.set_count(cmd, 2)
        self.assertTrue(self.validate(cmd), 'no scale point needed in a sketch')
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = self.copies()
        self.assertXYZ(made[0].geometry.center, (1.0, 0.0, 0.0), 'scaled about the moved centre: it stays put')
        self.assertAlmostEqual(made[0].radius, 0.4)
        self.assertXYZ(made[1].geometry.center, (2.0, 0.0, 0.0))
        self.assertAlmostEqual(made[1].radius, 0.6)

    def test_rotation_turns_the_copies_about_the_centre_of_the_selection(self):
        left, right = self.sketch.point('left', at=(0, 0)), self.sketch.point('right', at=(2, 0))
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, left, right)         # bounding box centre (1, 0)
        self.set_text(cmd, C.ID_ROTATION, '90')
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = self.copies()
        self.assertXYZ(made[0].geometry, (2.0, -1.0, 0.0))   # (0,0) turned about (1,0) -> (1,-1), then +10 mm
        self.assertXYZ(made[1].geometry, (2.0, 1.0, 0.0))

    def test_rotation_about_a_chosen_sketch_point(self):
        left, right = self.sketch.point('left', at=(0, 0)), self.sketch.point('right', at=(2, 0))
        hub = self.sketch.point('hub', at=(0, 0))
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, left, right)
        self.set_text(cmd, C.ID_ROTATION, '90')
        self.select(cmd, C.ID_ROTATION_AXIS, hub)
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = self.copies()
        self.assertXYZ(made[0].geometry, (1.0, 0.0, 0.0))
        self.assertXYZ(made[1].geometry, (1.0, 2.0, 0.0))
        stored = json.loads(made[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertEqual(stored['tokens']['rotation_axis'], hub.entityToken)

    def test_circular_pattern_with_a_varying_diameter_in_a_sketch(self):
        spoke_hole = self.sketch.circle('spoke hole', centre=(2, 0), radius=0.1)
        hub = self.sketch.point('hub', at=(0, 0))
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Circular')
        self.select(cmd, C.ID_OBJECTS, spoke_hole)
        self.select(cmd, C.ID_AXIS, hub)
        self.set_expression(cmd, C.ID_A_ANG, '90 deg')
        self.set_text(cmd, C.ID_SIZE, '4 + 2 * i')          # 6 mm, 8 mm
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        made = self.copies()
        self.assertXYZ(made[0].geometry.center, (0.0, 2.0, 0.0))
        self.assertXYZ(made[1].geometry.center, (-2.0, 0.0, 0.0))
        self.assertAlmostEqual(made[0].radius, 0.3)
        self.assertAlmostEqual(made[1].radius, 0.4)
        self.assertEqual(self.sketch.geometricConstraints.created, [])

    def test_curves_and_centre_from_another_sketch_are_refused(self):
        other = adsk.fusion.Sketch('Other')
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.hole, other.circle('stray'))
        self.set_text(cmd, C.ID_ROTATION, '10')
        self.assertFalse(self.preview(cmd).isValidResult)
        self.assertIn('same sketch', self.preview_text(cmd))
        cmd.commandInputs.itemById(C.ID_OBJECTS).clearSelection()
        self.select(cmd, C.ID_OBJECTS, self.hole)
        self.select(cmd, C.ID_ROTATION_AXIS, other.point('far'))
        self.assertFalse(self.preview(cmd).isValidResult)
        self.assertIn('rotation centre must be in the same sketch', self.preview_text(cmd))
        self.assertEqual(self.copies(), [])

    def test_proxies_are_resolved_to_the_sketch_objects(self):
        hub = self.sketch.point('hub', at=(0, 0))
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, self.hole.proxy('Plate:1'))
        self.set_text(cmd, C.ID_ROTATION, '10')
        self.select(cmd, C.ID_ROTATION_AXIS, hub.proxy('Plate:1'))
        self.set_count(cmd, 1)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(len(self.copies()), 1)


# ---------------------------------------------------------------------------
# Editing patterns made by the transform engine
# ---------------------------------------------------------------------------

class EditingTransformedPatterns(VaryHarness):
    def setUp(self):
        super().setUp()
        self.block = self.root.body('Block')
        self.x_edge = self.edge('x edge', (0, 0, 0), (1, 0, 0))
        self.y_edge = self.edge('y edge', (0, 0, 0), (0, 1, 0))
        self.z_axis = self.axis('z axis', (0, 0, 0), (0, 0, 1))
        self.corner = self.vertex('corner', (0, 0, 0))

    def make_path_bodies(self, count=3, group='Dots', vary=True):
        """A Path pattern of Block with (optionally) rotation and size; returns its features."""
        before = set(id(e) for e in self.timeline_entities())
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, self.block)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.select(cmd, C.ID_DIRECTION2, self.y_edge)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        if vary:
            self.set_text(cmd, C.ID_ROTATION, '10 * i')
            self.select(cmd, C.ID_ROTATION_AXIS, self.z_axis)
            self.set_text(cmd, C.ID_SIZE, '1 + 0.1 * i')
            self.select(cmd, C.ID_SCALE_POINT, self.corner)
        self.set_count(cmd, count)
        self.set_text(cmd, C.ID_GROUP_NAME, group)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        return [e for e in self.timeline_entities() if id(e) not in before]

    def make_native_linear(self, count=3, group='Frets'):
        cmd, dialog = self.open_dialog()
        self.select(cmd, C.ID_OBJECTS, 'slot')
        self.select(cmd, C.ID_DIRECTION, 'neck edge')
        self.set_count(cmd, count)
        self.set_text(cmd, C.ID_GROUP_NAME, group)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        return list(self.root.features.rectangularPatternFeatures.created)

    def test_a_transformed_pattern_is_listed_and_loads_everything_from_its_tokens(self):
        feats = self.make_path_bodies(count=3)
        self.assertEqual(len(feats), 9, 'copy, scale and move per copy')
        timeline = self.design.timeline
        timeline.markerPosition = 0                      # loading must not need the marker

        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Dots (3 copies)'], 'copies, not features')
        self.pick(cmd, 'Dots (3 copies)')
        self.assertTrue(dialog.editing)
        self.assertEqual(dialog.editing.kind, 'features')
        self.assertTrue(dialog.path)
        self.assertEqual(dialog.count, 3)
        self.assertEqual(dialog.formula2_text, '5 * i')
        self.assertEqual(dialog.rotation_text, '10 * i')
        self.assertEqual(dialog.size_text, '1 + 0.1 * i')
        self.assertEqual(dialog.group_name, 'Dots')
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS), [self.block])
        self.assertEqual(self.selected(cmd, C.ID_DIRECTION), [self.x_edge])
        self.assertEqual(self.selected(cmd, C.ID_DIRECTION2), [self.y_edge])
        self.assertEqual(self.selected(cmd, C.ID_ROTATION_AXIS), [self.z_axis])
        self.assertEqual(self.selected(cmd, C.ID_SCALE_POINT), [self.corner])
        self.assertEqual(self.highlighted(cmd), feats, 'every feature of every copy is highlighted')
        self.assertIn('Editing Dots (3 copies)', cmd.commandInputs.itemById(C.ID_EDIT_STATUS).text)
        self.assertTrue(self.validate(cmd))
        self.assertEqual(timeline.markerPosition, 0, 'read by token: the marker was not touched')
        self.assertFalse(self.preview(cmd).isValidResult, 'nothing changed: nothing is rebuilt')
        self.assertFalse(any(f._deleted for f in feats))

    def test_changing_the_count_rebuilds_in_place_with_the_same_id(self):
        other = self.root.features.otherFeatures
        other.add('Base')
        old = self.make_path_bodies(count=3, vary=False)
        other.add('Fillet1')
        old_bodies = [b for b in self.root.bRepBodies if b is not self.block]
        old_id = old[0].attributes.itemByName('FormulaPattern', 'pattern').value
        timeline = self.design.timeline
        self.assertEqual(timeline.names(), ['Base', ['Dots 1/3', 'Dots 1/3', 'Dots 2/3', 'Dots 2/3', 'Dots 3/3', 'Dots 3/3'], 'Fillet1'])

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Dots (3 copies)')
        self.set_count(cmd, 2)
        self.assertIn('OK replaces it', cmd.commandInputs.itemById(C.ID_EDIT_STATUS).text)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))

        self.assertTrue(all(f._deleted for f in old), 'the old features are gone')
        self.assertTrue(all(b._deleted for b in old_bodies), 'and so are the bodies they made')
        self.assertEqual(timeline.names(), ['Base', ['Dots 1/2', 'Dots 1/2', 'Dots 2/2', 'Dots 2/2'], 'Fillet1'])
        self.assertEqual(timeline.markerPosition, timeline.count, 'marker back at the end')
        self.assertEqual(timeline.timelineGroups.count, 1)
        new = [e for e in self.timeline_entities() if e.name.startswith('Dots')]
        self.assertEqual(new[0].attributes.itemByName('FormulaPattern', 'pattern').value, old_id, 'same pattern, edited')
        self.assertEqual(json.loads(new[0].attributes.itemByName('FormulaPattern', 'settings').value)['count'], 2)
        bodies = [b for b in self.root.bRepBodies if b is not self.block]
        self.assertEqual(len(bodies), 2)
        self.assertXYZ(bodies[1].location(), (2.0, 1.0, 0.0))
        self.assertEqual(self.highlighted(cmd), [], 'deleted copies are not left in the highlight')
        self.assertFalse(self.block._deleted)

    def test_stale_copies_in_the_record_are_looked_up_again_before_they_are_deleted(self):
        """Fusion handed back dead wrappers (no name, no timelineObject) for the copies of a
        pattern between loading it and rebuilding it (2026-11-08); the live ones are found by id."""
        old = self.make_native_linear(count=3)
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')

        class Dead:
            name = ''
            objectType = 'adsk::fusion::RectangularPatternFeature'
            timelineObject = None
            entityToken = None

            @property
            def attributes(self):
                raise RuntimeError('3 : InternalValidationError : dead')

            def deleteMe(self):
                raise AssertionError('a stale wrapper must not be deleted')

        dialog.editing.copies = [Dead() for _ in old]
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(f._deleted for f in old), 'the live features were deleted instead')
        self.assertEqual(self.design.timeline.names(), [['Frets 1/2', 'Frets 2/2']])

    def test_a_marker_the_user_rolled_back_stays_where_it_was(self):
        other = self.root.features.otherFeatures
        other.add('Base')
        self.make_path_bodies(count=2, vary=False)
        other.add('Fillet1')
        timeline = self.design.timeline
        timeline.markerPosition = 2
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Dots (2 copies)')
        self.set_count(cmd, 3)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertEqual(timeline.markerPosition, 2)
        self.assertEqual(timeline.names()[-1], 'Fillet1')

    def test_clicking_a_body_the_engine_made_picks_its_pattern(self):
        dots = self.make_path_bodies(count=2, vary=False)
        frets = self.make_native_linear(count=3)
        dot_body = [b for b in self.root.bRepBodies if b is not self.block][1]
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Dots (2 copies)', 'Frets (3 copies)'])
        self.pick(cmd, 'Frets (3 copies)')
        self.select(cmd, C.ID_EXISTING, dot_body)
        self.assertEqual(cmd.commandInputs.itemById(C.ID_PATTERN_LIST).selectedItem.name, 'Dots (2 copies)')
        self.assertEqual(dialog.editing.id, dots[0].attributes.itemByName('FormulaPattern', 'pattern').value)
        self.assertEqual(self.highlighted(cmd), dots)
        self.assertTrue(dialog.path)
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS), [self.block])
        self.assertTrue(cmd.commandInputs.itemById(C.ID_OBJECTS).hasFocus)
        self.assertFalse(any(f._deleted for f in frets))

    def test_a_component_pattern_is_listed_with_its_occurrences_and_rebuilt(self):
        wheel = self.design.add_component('Wheel', 'Wheel:1')
        original = self.root.occurrences.item(0)
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Components')
        self.select(cmd, C.ID_OBJECTS, original)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_ROTATION, '15 * i')
        self.select(cmd, C.ID_ROTATION_AXIS, self.z_axis)
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        old = [self.root.occurrences.item(1), self.root.occurrences.item(2)]
        self.design.add_component('Axle', 'Axle:1')     # something after the pattern

        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Formula Pattern (2 copies)'])
        self.pick(cmd, 'Formula Pattern (2 copies)')
        self.assertEqual(dialog.object_type, 'Components')
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS), [original])
        self.assertEqual(self.selected(cmd, C.ID_ROTATION_AXIS), [self.z_axis])
        self.assertEqual(self.highlighted(cmd), old)
        self.set_count(cmd, 3)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(o._deleted for o in old))
        self.assertEqual(self.root.occurrences.count, 5)
        self.assertEqual(self.design.timeline.names(),
                         ['Wheel:1', ['Wheel:4', 'Wheel:5', 'Wheel:6'], 'Axle:1'], 'rebuilt in place')
        self.assertEqual(self.design.timeline.markerPosition, self.design.timeline.count)
        self.assertFalse(original._deleted)

    def test_a_profiles_pattern_loads_its_target_and_rebuilds_the_cut(self):
        sketch = adsk.fusion.Sketch('Wells', self.root)
        well = sketch.profile('well')
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.select(cmd, C.ID_OBJECTS, well)
        self.select(cmd, C.ID_DIRECTION, self.x_edge)
        self.set_text(cmd, C.ID_DEPTH, 'a * i')
        self.choose(cmd, C.ID_OPERATION, 'Cut')
        self.select(cmd, C.ID_TARGET, self.block)
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        old = self.timeline_entities()
        self.assertEqual(len(old), 5)

        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Formula Pattern (2 copies)')
        self.assertEqual(dialog.object_type, 'Profiles')
        self.assertEqual(dialog.depth_text, 'a * i')
        self.assertEqual(dialog.operation, 2)
        self.assertTrue(self.visible(cmd, C.ID_TARGET))
        self.assertEqual(self.selected(cmd, C.ID_TARGET), [self.block])
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS), [well])
        self.assertTrue(self.validate(cmd))
        self.set_count(cmd, 3)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(f._deleted for f in old))
        rows = [r.objectType.split('::')[-1] for r in self.timeline_entities()]
        self.assertEqual(rows, ['ExtrudeFeature', 'MoveFeature'] * 3 + ['CombineFeature'])
        self.assertEqual([b.name for b in self.root.bRepBodies], ['Block'])
        self.assertFalse(self.block._deleted)

    def test_a_sketch_copies_pattern_is_listed_loaded_and_rebuilt_without_orphans(self):
        sketch = adsk.fusion.Sketch('Holes')
        hole = sketch.circle('hole', centre=(0, 0), radius=0.2)
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, hole)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_text(cmd, C.ID_SIZE, 'a + 2 * i')
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)
        old = list(sketch.copies)
        self.assertEqual(len(old), 2)

        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Holes sketch pattern (2 copies)'])
        self.pick(cmd, 'Holes sketch pattern (2 copies)')
        self.assertEqual(dialog.editing.kind, 'sketch_copies')
        self.assertEqual(dialog.object_type, 'Sketch curves')
        self.assertTrue(dialog.path)
        self.assertEqual(dialog.size_kind, C.engine.SIZE_DIAMETER)
        self.assertEqual(dialog.size_text, 'a + 2 * i')
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS), [hole])
        self.assertEqual(self.selected(cmd, C.ID_DIRECTION), [])
        self.assertEqual(self.highlighted(cmd), old, 'the copied circles are highlighted')
        self.assertTrue(self.validate(cmd))
        self.assertFalse(self.preview(cmd).isValidResult, 'unchanged')

        self.set_count(cmd, 3)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.assertTrue(all(c._deleted for c in old))
        self.assertEqual(sketch.deleted, old, 'the old copies are deleted, not left behind')
        new = [c for c in sketch.copies if not c._deleted]
        self.assertEqual(len(new), 3)
        self.assertFalse(hole._deleted)
        self.assertAlmostEqual(new[2].radius, 0.8)
        self.assertEqual(new[0].attributes.itemByName('FormulaPattern', 'pattern').value, dialog.editing.id)
        # clicking a copied circle picks the pattern
        self.finish(cmd)
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Holes sketch pattern (3 copies)'])
        self.pick(cmd, 'Holes sketch pattern (3 copies)')
        self.pick(cmd, 'New pattern')
        self.pick(cmd, 'Holes sketch pattern (3 copies)')
        self.select(cmd, C.ID_EXISTING, new[1])
        self.assertEqual(len(self.highlighted(cmd)), 3)

    def test_a_sketch_copies_pattern_is_listed_only_while_its_sketch_is_edited(self):
        # Outside the sketch its copies cannot be deleted (seen in Fusion 2026-11-08: OK left
        # the old copies behind and built the new ones on top), so it is not offered there.
        sketch = adsk.fusion.Sketch('Holes')
        hole = sketch.circle('hole', centre=(0, 0), radius=0.2)
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.select(cmd, C.ID_OBJECTS, hole)
        self.set_text(cmd, C.ID_FORMULA2, '5 * i')
        self.set_count(cmd, 2)
        self.assertTrue(self.preview(cmd).isValidResult, self.preview_text(cmd))
        self.finish(cmd)

        cancelled = adsk.core.CommandTerminationReason.CancelledTerminationReason
        self.app.activeEditObject = None                 # back in the solid environment
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern'])
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, cancelled))
        self.app.activeEditObject = adsk.fusion.Sketch('Other')
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern'])
        cmd.destroy.fire(adsk.core.CommandEventArgs(cmd, cancelled))
        self.app.activeEditObject = sketch
        cmd, dialog = self.open_dialog()
        self.assertEqual(self.list_names(cmd), ['New pattern', 'Holes sketch pattern (2 copies)'])

    def test_a_feature_whose_timeline_row_cannot_be_asked_is_found_by_scanning_the_timeline(self):
        self.make_native_linear(count=2)
        feature = self.root.features.rectangularPatternFeatures.item(1)
        group = feature.timelineObject.parentGroup
        self.assertIsNotNone(group)
        expected = C._timeline_index(feature)

        class Row:                                   # a timeline row that answers nothing useful
            parentGroup = None

            @property
            def index(self):
                raise RuntimeError('2 : InternalValidationError : res >= 0')

        class Stub:
            timelineObject = Row()
            entityToken = feature.entityToken
            objectType = feature.objectType
            name = feature.name

        self.assertEqual(C._timeline_index(Stub()), expected)

    def test_an_expanded_group_is_collapsed_to_find_its_row(self):
        self.make_native_linear(count=2)
        feature = self.root.features.rectangularPatternFeatures.item(0)
        group = feature.timelineObject.parentGroup
        collapsed_index = group.index
        group.isCollapsed = False
        self.assertEqual(C._timeline_index(feature), collapsed_index)

    def test_native_patterns_keep_rolling_the_marker_to_read_their_inputs(self):
        feats = self.make_native_linear(count=3)
        stored = json.loads(feats[0].attributes.itemByName('FormulaPattern', 'settings').value)
        self.assertNotIn('tokens', stored, 'native patterns are read as in 1.1')
        cmd, dialog = self.open_dialog()
        self.pick(cmd, 'Frets (3 copies)')
        self.assertEqual(self.selected(cmd, C.ID_OBJECTS)[0].name, 'slot')
        self.assertEqual(self.selected(cmd, C.ID_DIRECTION)[0].name, 'neck edge')


class SettingsVary(VaryHarness):
    def test_new_values_are_remembered_without_tokens_and_restored(self):
        cmd, dialog = self.open_dialog()
        self.choose(cmd, C.ID_TYPE, 'Path')
        self.choose(cmd, C.ID_PRESET, 'Sunflower (phyllotaxis)')
        self.set_text(cmd, C.ID_ROTATION, '137.5 * i')
        self.set_text(cmd, C.ID_SIZE, 'a / 2')
        self.choose(cmd, C.ID_SIZE_KIND, 'Diameter (circles)')
        self.choose(cmd, C.ID_OBJECT_TYPE, 'Profiles')
        self.set_text(cmd, C.ID_DEPTH, 'a * t')
        self.choose(cmd, C.ID_OPERATION, 'Join')
        self.select(cmd, C.ID_TARGET, self.root.body('Block'))
        self.finish(cmd)
        saved = self.saved[0]
        self.assertEqual(saved['pattern_type'], 'path')
        self.assertEqual(saved['coords'], 'polar')
        self.assertEqual(saved['formula2'], 'i * 137.508')
        self.assertEqual(saved['rotation'], '137.5 * i')
        self.assertEqual(saved['size'], 'a / 2')
        self.assertEqual(saved['size_kind'], 'diameter')
        self.assertEqual(saved['depth'], 'a * t')
        self.assertEqual(saved['operation'], 1)
        self.assertNotIn('tokens', saved, 'the settings file never names entities')

        with mock.patch.object(test_command.command, 'load_settings', return_value=saved):
            cmd, dialog = self.open_dialog()
        self.assertTrue(dialog.path)
        self.assertTrue(dialog.polar)
        self.assertEqual(dialog.preset_id, 'phyllotaxis')
        self.assertEqual(dialog.formula2_text, 'i * 137.508')
        self.assertEqual(dialog.rotation_text, '137.5 * i')
        self.assertEqual(dialog.size_text, 'a / 2')
        self.assertEqual(dialog.size_kind, C.engine.SIZE_DIAMETER)
        self.assertEqual(dialog.object_type, 'Profiles')
        self.assertEqual(dialog.depth_text, 'a * t')
        self.assertEqual(dialog.operation, 1)
        self.assertTrue(self.visible(cmd, C.ID_TARGET))
        self.assertTrue(self.visible(cmd, C.ID_DIRECTION2))
        self.assertIn('Formula = radius', cmd.commandInputs.itemById(C.ID_NOTES).text)

    def test_unknown_keys_and_garbage_values_are_ignored(self):
        stored = {'tokens': {'objects': ['nope']}, 'bogus': 1, 'size_kind': 'weird', 'operation': 'x',
                  'coords': 'spherical', 'pattern_type': 'path', 'rotation': 7}
        with mock.patch.object(test_command.command, 'load_settings', return_value=stored):
            cmd, dialog = self.open_dialog()
        self.assertTrue(dialog.path)
        self.assertEqual(dialog.size_kind, C.engine.SIZE_SCALE)
        self.assertEqual(dialog.operation, 0)
        self.assertFalse(dialog.polar)
        self.assertEqual(dialog.rotation_text, '7')
        self.assertNotIn('Traceback', self.preview_text(cmd))


if __name__ == '__main__':
    unittest.main()
