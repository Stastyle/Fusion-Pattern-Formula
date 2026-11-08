"""Tests for the fake adsk package itself (tests/fake_adsk).

The fake has to be honest about the parts of Fusion the transform engine relies on:
matrix maths, bodies that remember where they were moved, feature collections that go
through the timeline, sketch copies with geometry, and entity tokens. These tests pin
that behaviour down so the add-in tests can trust it.

Run from the repo root:  python -m unittest discover -s tests -v
"""
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fake_adsk'))

import adsk.core  # noqa: E402  (the fake)
import adsk.fusion  # noqa: E402

core, fusion = adsk.core, adsk.fusion


def P(x, y, z=0.0):
    return core.Point3D.create(x, y, z)


def V(x, y, z=0.0):
    return core.Vector3D.create(x, y, z)


def rotation_z(degrees, origin=(0.0, 0.0, 0.0)):
    m = core.Matrix3D.create()
    m.setToRotation(math.radians(degrees), V(0, 0, 1), P(*origin))
    return m


def translation(dx, dy, dz=0.0):
    m = core.Matrix3D.create()
    m.translation = V(dx, dy, dz)
    return m


def xyz(point):
    return (point.x, point.y, point.z)


class FakeCase(unittest.TestCase):
    def setUp(self):
        core.Application.reset()
        self.design = fusion.Design('mm')
        self.root = self.design.rootComponent
        self.features = self.root.features
        self.timeline = self.design.timeline

    def assertXYZ(self, actual, expected, msg=None, places=9):
        actual = xyz(actual) if hasattr(actual, 'x') else tuple(actual)
        self.assertEqual(len(actual), 3)
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, places=places, msg=f'{actual} != {expected}' + (f': {msg}' if msg else ''))


# ---- core geometry ----

class Matrix3DMaths(FakeCase):
    def test_create_is_identity(self):
        m = core.Matrix3D.create()
        self.assertEqual(m.asArray(), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        self.assertEqual(m.getCell(0, 0), 1.0)
        self.assertEqual(m.getCell(0, 3), 0.0)

    def test_rotation_of_90_degrees_about_z_moves_x_to_y(self):
        p = P(1, 0, 0)
        p.transformBy(rotation_z(90))
        self.assertXYZ(p, (0, 1, 0))

    def test_rotation_about_an_axis_through_a_point_leaves_that_point_alone(self):
        m = rotation_z(90, origin=(2, 0, 0))
        fixed = P(2, 0, 0)
        fixed.transformBy(m)
        self.assertXYZ(fixed, (2, 0, 0))
        moved = P(3, 0, 0)       # 1 along x from the centre -> 1 along y from the centre
        moved.transformBy(m)
        self.assertXYZ(moved, (2, 1, 0))

    def test_rotation_about_an_arbitrary_axis_uses_rodrigues(self):
        m = core.Matrix3D.create()
        axis = V(1, 1, 0)
        m.setToRotation(math.pi, axis, P(0, 0, 0))   # half turn about the diagonal swaps x and y
        p = P(1, 0, 0)
        p.transformBy(m)
        self.assertXYZ(p, (0, 1, 0))

    def test_translation_property_reads_and_writes_the_last_column(self):
        m = translation(1, 2, 3)
        self.assertEqual(m.asArray()[3], 1.0)
        self.assertEqual(m.asArray()[7], 2.0)
        self.assertEqual(m.asArray()[11], 3.0)
        self.assertXYZ(m.translation, (1, 2, 3))
        p = P(1, 1, 1)
        p.transformBy(m)
        self.assertXYZ(p, (2, 3, 4))

    def test_set_with_array_round_trips(self):
        cells = [float(k) for k in range(16)]
        m = core.Matrix3D.create()
        self.assertTrue(m.setWithArray(cells))
        self.assertEqual(m.asArray(), cells)
        self.assertEqual(m.getCell(1, 2), 6.0)
        m.setCell(1, 2, 60.0)
        self.assertEqual(m.asArray()[6], 60.0)
        with self.assertRaises(ValueError):
            m.setWithArray([1.0, 2.0])

    def test_invert_undoes_a_rotation_and_translation(self):
        m = rotation_z(30)
        m.transformBy(translation(5, -2, 1))
        inverse = m.copy()
        self.assertTrue(inverse.invert())
        product = m.copy()
        product.transformBy(inverse)       # inverse · m
        self.assertTrue(product.isEqualTo(core.Matrix3D.create()))
        singular = core.Matrix3D.create()
        singular.setWithArray([0.0] * 16)
        self.assertFalse(singular.invert())

    def test_transform_by_applies_the_argument_after_self(self):
        # self = matrix · self: rotate first, then translate.
        m = rotation_z(90)
        m.transformBy(translation(10, 0, 0))
        p = P(1, 0, 0)
        p.transformBy(m)
        self.assertXYZ(p, (10, 1, 0))
        # The other order translates first, then rotates the translated point.
        m2 = translation(10, 0, 0)
        m2.transformBy(rotation_z(90))
        p2 = P(1, 0, 0)
        p2.transformBy(m2)
        self.assertXYZ(p2, (0, 11, 0))

    def test_copy_is_independent_and_is_equal_to_uses_a_tolerance(self):
        m = translation(1, 0, 0)
        c = m.copy()
        c.setCell(0, 3, 2.0)
        self.assertEqual(m.getCell(0, 3), 1.0)
        near = translation(1 + 1e-12, 0, 0)
        self.assertTrue(m.isEqualTo(near))
        far = translation(1 + 1e-6, 0, 0)
        self.assertFalse(m.isEqualTo(far))

    def test_set_with_coordinate_system_puts_the_axes_in_the_columns(self):
        m = core.Matrix3D.create()
        m.setWithCoordinateSystem(P(1, 2, 3), V(0, 1, 0), V(-1, 0, 0), V(0, 0, 1))
        p = P(1, 0, 0)
        p.transformBy(m)
        self.assertXYZ(p, (1, 3, 3))     # x axis maps onto +y, plus the origin
        origin, x, y, z = m.getAsCoordinateSystem()
        self.assertXYZ(origin, (1, 2, 3))
        self.assertXYZ(x, (0, 1, 0))
        self.assertXYZ(y, (-1, 0, 0))
        self.assertXYZ(z, (0, 0, 1))

    def test_set_to_identity_and_determinant(self):
        m = rotation_z(45)
        self.assertAlmostEqual(m.determinant, 1.0)
        m.setToIdentity()
        self.assertEqual(m.asArray(), core.Matrix3D.create().asArray())


class PointsAndVectors(FakeCase):
    def test_point_transform_includes_translation_but_vector_ignores_it(self):
        m = rotation_z(90)
        m.transformBy(translation(10, 0, 0))
        p, v = P(1, 0, 0), V(1, 0, 0)
        p.transformBy(m)
        v.transformBy(m)
        self.assertXYZ(p, (10, 1, 0))
        self.assertXYZ(v, (0, 1, 0))

    def test_vector_arithmetic(self):
        v = V(3, 4, 0)
        self.assertAlmostEqual(v.length, 5.0)
        self.assertTrue(v.normalize())
        self.assertXYZ(v, (0.6, 0.8, 0))
        self.assertAlmostEqual(V(1, 0, 0).dotProduct(V(0, 1, 0)), 0.0)
        self.assertXYZ(V(1, 0, 0).crossProduct(V(0, 1, 0)), (0, 0, 1))
        a = V(1, 2, 3)
        a.add(V(1, 1, 1))
        self.assertXYZ(a, (2, 3, 4))
        a.subtract(V(2, 2, 2))
        self.assertXYZ(a, (0, 1, 2))
        a.scaleBy(2)
        self.assertXYZ(a, (0, 2, 4))
        self.assertEqual(a.asArray(), [0, 2, 4])
        self.assertFalse(V(0, 0, 0).normalize(), 'a zero vector cannot be normalised')
        self.assertTrue(V(1, 0, 0).isParallelTo(V(-2, 0, 0)))
        self.assertTrue(V(1, 0, 0).isPerpendicularTo(V(0, 3, 0)))
        self.assertAlmostEqual(V(1, 0, 0).angleTo(V(0, 1, 0)), math.pi / 2)

    def test_point_arithmetic(self):
        p = P(1, 2, 3)
        self.assertAlmostEqual(p.distanceTo(P(1, 2, 7)), 4.0)
        self.assertXYZ(p.vectorTo(P(2, 4, 6)), (1, 2, 3))
        p.translateBy(V(1, 1, 1))
        self.assertXYZ(p, (2, 3, 4))
        q = p.copy()
        q.x = 99
        self.assertEqual(p.x, 2.0)
        self.assertTrue(p.isEqualTo(P(2, 3, 4)))
        self.assertFalse(p.isEqualTo(P(2, 3, 4.001)))
        self.assertXYZ(p.asVector(), (2, 3, 4))
        self.assertXYZ(V(5, 6, 7).asPoint(), (5, 6, 7))
        p.setWithArray([7, 8, 9])
        self.assertEqual(p.asArray(), [7, 8, 9])
        ok, x, y, z = p.getData()
        self.assertEqual((ok, x, y, z), (True, 7, 8, 9))


class OtherGeometry(FakeCase):
    def test_bounding_box_combine_expand_contains(self):
        box = core.BoundingBox3D.create(P(0, 0, 0), P(1, 1, 1))
        self.assertTrue(box.contains(P(0.5, 0.5, 0.5)))
        self.assertFalse(box.contains(P(2, 0, 0)))
        box.expand(P(2, -1, 0))
        self.assertXYZ(box.minPoint, (0, -1, 0))
        self.assertXYZ(box.maxPoint, (2, 1, 1))
        box.combine(core.BoundingBox3D.create(P(-5, 0, 0), P(0, 0, 5)))
        self.assertXYZ(box.minPoint, (-5, -1, 0))
        self.assertXYZ(box.maxPoint, (2, 1, 5))
        self.assertTrue(box.intersects(core.BoundingBox3D.create(P(1, 0, 0), P(9, 9, 9))))
        self.assertFalse(box.intersects(core.BoundingBox3D.create(P(10, 0, 0), P(11, 1, 1))))
        other = box.copy()
        other.expand(P(100, 0, 0))
        self.assertXYZ(box.maxPoint, (2, 1, 5), 'copy is independent')

    def test_lines_and_circles(self):
        line = core.Line3D.create(P(0, 0, 0), P(0, 2, 0))
        self.assertXYZ(line.startPoint, (0, 0, 0))
        self.assertXYZ(line.endPoint, (0, 2, 0))
        infinite = line.asInfiniteLine()
        self.assertXYZ(infinite.origin, (0, 0, 0))
        self.assertXYZ(infinite.direction, (0, 1, 0))
        axis = core.InfiniteLine3D.create(P(1, 1, 1), V(0, 0, 3))
        self.assertXYZ(axis.direction, (0, 0, 3), 'create keeps the direction as given')
        circle = core.Circle3D.createByCenter(P(1, 2, 0), V(0, 0, 1), 2.5)
        self.assertXYZ(circle.center, (1, 2, 0))
        self.assertXYZ(circle.normal, (0, 0, 1))
        self.assertEqual(circle.radius, 2.5)
        copy = line.copy()
        copy.startPoint = P(9, 9, 9)
        self.assertXYZ(line.startPoint, (0, 0, 0))

    def test_object_collection_helpers(self):
        a, b = object(), object()
        c = core.ObjectCollection.createWithArray([a, b])
        self.assertEqual(c.count, 2)
        self.assertEqual(len(c), 2)
        self.assertIs(c[1], b)
        self.assertTrue(c.contains(a))
        self.assertTrue(c.removeByItem(a))
        self.assertEqual(c.asArray(), [b])
        c.clear()
        self.assertEqual(c.count, 0)


# ---- entities with geometry ----

class EntityGeometry(FakeCase):
    def test_a_linear_edge_has_a_line_from_start_to_end(self):
        edge = fusion.Entity('edge', 'BRepEdge', start=(1, 1, 0), end=(1, 4, 0))
        g = edge.geometry
        self.assertIsInstance(g, core.Line3D)
        self.assertXYZ(g.startPoint, (1, 1, 0))
        self.assertXYZ(g.endPoint, (1, 4, 0))

    def test_a_construction_axis_has_an_infinite_line_with_a_unit_direction(self):
        axis = fusion.Entity('axis', 'ConstructionAxis', start=(0, 0, 1), end=(0, 0, 4))
        g = axis.geometry
        self.assertIsInstance(g, core.InfiniteLine3D)
        self.assertXYZ(g.origin, (0, 0, 1))
        self.assertXYZ(g.direction, (0, 0, 1))

    def test_a_vertex_and_a_construction_point_have_a_point(self):
        vertex = fusion.Entity('corner', 'BRepVertex', start=(2, 3, 4))
        self.assertIsInstance(vertex.geometry, core.Point3D)
        self.assertXYZ(vertex.geometry, (2, 3, 4))
        point = fusion.Entity('origin', 'ConstructionPoint', start=(1, 1, 1))
        self.assertXYZ(point.geometry, (1, 1, 1))

    def test_defaults_keep_geometry_free_tests_working(self):
        edge = fusion.Entity('edge')
        self.assertEqual(edge.objectType, 'adsk::fusion::BRepEdge')
        self.assertXYZ(edge.geometry.startPoint, (0, 0, 0))
        self.assertXYZ(edge.geometry.endPoint, (1, 0, 0))
        body = fusion.Entity('slot', 'BRepBody', self.root)
        self.assertIs(body.parentComponent, self.root)
        self.assertTrue(body.entityToken)
        face = fusion.Entity('face', 'BRepFace', body=body)
        self.assertIs(face.body, body)
        self.assertIsNone(face.geometry, 'a face has no line or point geometry in the fake')

    def test_a_proxy_shares_the_token_of_its_native_entity(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        body = fusion.Entity('Body1', 'BRepBody', neck)
        occurrence = self.root.allOccurrencesByComponent(neck).item(0)
        proxy = body.createForAssemblyContext(occurrence)
        self.assertIs(proxy.nativeObject, body)
        self.assertIs(proxy.assemblyContext, occurrence)
        self.assertEqual(proxy.entityToken, body.entityToken)


# ---- bodies and the transform features ----

class Bodies(FakeCase):
    def test_a_body_knows_its_component_and_starts_where_it_was_made(self):
        body = self.root.body('Body1', origin=(1, 2, 3))
        self.assertEqual(body.objectType, 'adsk::fusion::BRepBody')
        self.assertIs(body.parentComponent, self.root)
        self.assertEqual(body.location(), (1, 2, 3))
        self.assertEqual(body._scale, 1.0)
        self.assertEqual(body._transform, core.Matrix3D.create().asArray())
        self.assertXYZ(body.physicalProperties.centerOfMass, (1, 2, 3))
        self.assertTrue(body.boundingBox.contains(P(1, 2, 3)))
        self.assertEqual(self.root.bRepBodies.count, 1)
        self.assertIs(self.root.bRepBodies.item(0), body)
        self.assertIs(self.root.bRepBodies.itemByName('Body1'), body)
        self.assertTrue(body.entityToken)
        self.assertEqual(body.attributes.count, 0)

    def test_delete_me_removes_the_body_from_its_component(self):
        body = self.root.body('Body1')
        self.assertTrue(body.deleteMe())
        self.assertTrue(body._deleted)
        self.assertEqual(self.root.bRepBodies.count, 0)
        with self.assertRaises(RuntimeError):
            body.deleteMe()

    def test_a_proxy_body_follows_the_native_body(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        body = neck.body('Body1')
        occurrence = self.root.allOccurrencesByComponent(neck).item(0)
        proxy = body.createForAssemblyContext(occurrence)
        self.assertIs(proxy.nativeObject, body)
        self.assertEqual(occurrence.bRepBodies.count, 1)
        self.assertIs(occurrence.bRepBodies.item(0).nativeObject, body)
        self.move([body], translation(5, 0, 0))
        self.assertEqual(proxy.location(), (5, 0, 0), 'a proxy is a live view of the body')
        body.deleteMe()
        self.assertTrue(proxy._deleted)

    def move(self, bodies, matrix):
        entities = core.ObjectCollection.create()
        for b in bodies:
            entities.add(b)
        move_input = self.features.moveFeatures.createInput2(entities)
        move_input.defineAsFreeMove(matrix)
        return self.features.moveFeatures.add(move_input)


class MoveFeatures(Bodies):
    def test_a_free_move_changes_where_the_body_is(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        m = rotation_z(90)
        m.transformBy(translation(0, 0, 5))
        feature = self.move([body], m)
        self.assertEqual(feature.objectType, 'adsk::fusion::MoveFeature')
        self.assertXYZ(body.location(), (0, 1, 5))
        with self.assertRaises(RuntimeError):
            feature.inputEntities        # as in Fusion: roll the marker to before the feature first
        feature.timelineObject.rollTo(True)
        self.assertIs(feature.inputEntities.item(0), body)
        self.timeline.moveToEnd()
        self.assertEqual(feature.timelineObject.entity, feature)
        self.assertEqual(self.timeline.names(), [feature.name])
        self.assertEqual(self.features.moveFeatures.count, 1)
        self.assertIs(self.features.moveFeatures.item(0), feature)
        self.assertTrue(feature.transform.isEqualTo(m))

    def test_moves_compose_in_order(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        self.move([body], translation(1, 0, 0))     # (2, 0, 0)
        self.move([body], rotation_z(90))           # (0, 2, 0)
        self.assertXYZ(body.location(), (0, 2, 0))

    def test_free_move_rejects_a_matrix_with_scaling(self):
        scaled = core.Matrix3D.create()
        scaled.setCell(0, 0, 2.0)
        move_input = self.features.moveFeatures.createInput2(core.ObjectCollection.create())
        with self.assertRaises(RuntimeError) as caught:
            move_input.defineAsFreeMove(scaled)
        self.assertEqual(str(caught.exception), '3 : invalid argument transform')
        sheared = core.Matrix3D.create()
        sheared.setCell(0, 1, 0.5)
        with self.assertRaises(RuntimeError):
            move_input.defineAsFreeMove(sheared)

    def test_free_move_accepts_a_rotation_with_translation(self):
        m = rotation_z(37, origin=(1, 2, 3))
        m.transformBy(translation(4, 5, 6))
        move_input = self.features.moveFeatures.createInput2(core.ObjectCollection.create())
        self.assertTrue(move_input.defineAsFreeMove(m))
        self.assertTrue(move_input.transform.isEqualTo(m))

    def test_add_without_a_definition_is_refused(self):
        move_input = self.features.moveFeatures.createInput2(core.ObjectCollection.create())
        with self.assertRaises(RuntimeError):
            self.features.moveFeatures.add(move_input)

    def test_define_as_rotate_turns_about_the_axis_entity(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        axis = fusion.Entity('z axis', 'ConstructionAxis', start=(0, 0, 0), end=(0, 0, 1))
        entities = core.ObjectCollection.create()
        entities.add(body)
        move_input = self.features.moveFeatures.createInput2(entities)
        self.assertTrue(move_input.defineAsRotate(axis, core.ValueInput.createByString('90 deg')))
        self.features.moveFeatures.add(move_input)
        self.assertXYZ(body.location(), (0, 1, 0))
        edge = fusion.Entity('edge', 'BRepEdge', start=(2, 0, 0), end=(2, 0, 9))
        move_input = self.features.moveFeatures.createInput2(entities)
        move_input.defineAsRotate(edge, core.ValueInput.createByReal(math.pi))
        self.features.moveFeatures.add(move_input)
        self.assertXYZ(body.location(), (4, -1, 0))

    def test_define_as_translate_xyz_uses_centimetres_or_the_document_units(self):
        body = self.root.body('Body1')
        entities = core.ObjectCollection.create()
        entities.add(body)
        move_input = self.features.moveFeatures.createInput2(entities)
        move_input.defineAsTranslateXYZ(core.ValueInput.createByReal(1.0),
                                        core.ValueInput.createByString('20 mm'),
                                        core.ValueInput.createByString('3'), True)   # '3' = 3 mm here
        self.features.moveFeatures.add(move_input)
        self.assertXYZ(body.location(), (1, 2, 0.3))

    def test_deleting_a_move_puts_the_body_back(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        feature = self.move([body], rotation_z(90))
        self.assertXYZ(body.location(), (0, 1, 0))
        feature.deleteMe()
        self.assertXYZ(body.location(), (1, 0, 0))
        self.assertEqual(self.timeline.names(), [])
        self.assertEqual(self.features.moveFeatures.count, 0)

    def test_a_move_can_also_move_an_occurrence(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        occurrence = self.root.allOccurrencesByComponent(neck).item(0)
        entities = core.ObjectCollection.create()
        entities.add(occurrence)
        move_input = self.features.moveFeatures.createInput2(entities)
        move_input.defineAsFreeMove(translation(0, 7, 0))
        self.features.moveFeatures.add(move_input)
        self.assertXYZ(occurrence.transform2.translation, (0, 7, 0))


class ScaleFeatures(Bodies):
    def scale(self, body, point, factor):
        entities = core.ObjectCollection.create()
        entities.add(body)
        scale_input = self.features.scaleFeatures.createInput(entities, point, factor)
        return self.features.scaleFeatures.add(scale_input)

    def test_a_scale_multiplies_the_scale_of_the_body(self):
        body = self.root.body('Body1')
        point = fusion.Entity('origin', 'ConstructionPoint', start=(0, 0, 0))
        feature = self.scale(body, point, core.ValueInput.createByReal(2.0))
        self.assertEqual(feature.objectType, 'adsk::fusion::ScaleFeature')
        self.assertEqual(body._scale, 2.0)
        self.scale(body, point, core.ValueInput.createByString('1.5'))
        self.assertEqual(body._scale, 3.0)
        self.assertEqual(body.location(), (0, 0, 0), 'scaling about its own origin leaves it in place')
        self.assertIs(feature.point, point)
        self.assertTrue(feature.isUniform)
        self.assertEqual(self.timeline.names(), [feature.name, self.features.scaleFeatures.item(1).name])

    def test_scaling_about_another_point_moves_the_body_away_from_it(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        point = fusion.Entity('corner', 'BRepVertex', start=(0, 0, 0))
        self.scale(body, point, core.ValueInput.createByReal(3.0))
        self.assertXYZ(body.location(), (3, 0, 0))
        self.assertEqual(body._scale, 3.0)
        box = body.boundingBox
        self.assertAlmostEqual(box.maxPoint.x - box.minPoint.x, 3.0, msg='the box grows with the scale')

    def test_scale_accepts_a_sketch_point_and_a_point3d(self):
        body = self.root.body('Body1', origin=(2, 0, 0))
        sketch = fusion.Sketch('Sketch1')
        self.scale(body, sketch.point('centre', at=(1, 0)), core.ValueInput.createByReal(2.0))
        self.assertXYZ(body.location(), (3, 0, 0))
        self.scale(body, P(3, 0, 0), core.ValueInput.createByReal(0.5))
        self.assertXYZ(body.location(), (3, 0, 0))
        self.assertEqual(body._scale, 1.0)

    def test_deleting_a_scale_undoes_it(self):
        body = self.root.body('Body1', origin=(1, 0, 0))
        feature = self.scale(body, P(0, 0, 0), core.ValueInput.createByReal(2.0))
        feature.deleteMe()
        self.assertEqual(body._scale, 1.0)
        self.assertXYZ(body.location(), (1, 0, 0))


class CopyPasteBodies(Bodies):
    def test_add_makes_a_new_body_at_the_same_place(self):
        body = self.root.body('Body1', origin=(1, 2, 3))
        self.move([body], rotation_z(90))
        feature = self.features.copyPasteBodies.add(body)
        self.assertEqual(feature.objectType, 'adsk::fusion::CopyPasteBody')
        self.assertEqual(feature.bodies.count, 1)
        copy = feature.bodies.item(0)
        self.assertIsNot(copy, body)
        self.assertIs(copy.parentComponent, self.root)
        self.assertXYZ(copy.location(), body.location())
        self.assertEqual(copy._transform, body._transform)
        self.assertEqual(self.root.bRepBodies.count, 2)
        self.assertEqual(feature.sourceBody.count, 1)
        self.assertIs(feature.sourceBody.item(0), body)
        self.assertNotEqual(copy.entityToken, body.entityToken)
        self.assertEqual(self.timeline.names()[-1], feature.name)
        self.move([copy], translation(1, 0, 0))
        self.assertXYZ(copy.location(), (-1, 1, 3))
        self.assertXYZ(body.location(), (-2, 1, 3), 'moving the copy leaves the original alone')

    def test_add_accepts_a_collection_of_bodies(self):
        a, b = self.root.body('A'), self.root.body('B', origin=(5, 5, 5))
        bodies = core.ObjectCollection.create()
        bodies.add(a)
        bodies.add(b)
        feature = self.features.copyPasteBodies.add(bodies)
        self.assertEqual(feature.bodies.count, 2)
        self.assertEqual(feature.bodies.item(1).location(), (5, 5, 5))

    def test_deleting_the_feature_deletes_the_copy(self):
        body = self.root.body('Body1')
        feature = self.features.copyPasteBodies.add(body)
        copy = feature.bodies.item(0)
        feature.deleteMe()
        self.assertTrue(copy._deleted)
        self.assertEqual(self.root.bRepBodies.count, 1)
        self.assertFalse(body._deleted)


class ExtrudeFeatures(Bodies):
    def extrude(self, profiles, depth, direction=fusion.ExtentDirections.PositiveExtentDirection,
                operation=fusion.FeatureOperations.NewBodyFeatureOperation):
        extrude_input = self.features.extrudeFeatures.createInput(profiles, operation)
        extent = fusion.DistanceExtentDefinition.create(depth)
        self.assertTrue(extrude_input.setOneSideExtent(extent, direction))
        return self.features.extrudeFeatures.add(extrude_input)

    def test_an_extrude_makes_one_body_at_the_profile_centroid(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        profile = sketch.profile('square', centroid=(2, 3, 0))
        self.assertEqual(profile.objectType, 'adsk::fusion::Profile')
        self.assertIs(profile.parentSketch, sketch)
        self.assertXYZ(profile.areaProperties().centroid, (2, 3, 0))
        self.assertTrue(profile.boundingBox.contains(P(2, 3, 0)))
        self.assertTrue(profile.entityToken)
        feature = self.extrude(profile, core.ValueInput.createByReal(0.8))
        self.assertEqual(feature.objectType, 'adsk::fusion::ExtrudeFeature')
        self.assertEqual(feature.bodies.count, 1)
        body = feature.bodies.item(0)
        self.assertIs(body.parentComponent, self.root)
        self.assertXYZ(body.location(), (2, 3, 0))
        self.assertAlmostEqual(feature.depth, 0.8)
        self.assertEqual(feature.operation, fusion.FeatureOperations.NewBodyFeatureOperation)
        self.assertIs(feature.profile, profile)
        self.assertEqual(self.root.bRepBodies.count, 1)
        self.assertEqual(self.timeline.names(), [feature.name])

    def test_negative_direction_records_a_negative_depth_and_strings_are_parsed(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        profile = sketch.profile('square')
        feature = self.extrude(profile, core.ValueInput.createByString('8 mm'),
                               fusion.ExtentDirections.NegativeExtentDirection)
        self.assertAlmostEqual(feature.depth, -0.8)
        feature = self.extrude(profile, core.ValueInput.createByString('0.8 cm'))
        self.assertAlmostEqual(feature.depth, 0.8)
        feature = self.extrude(profile, core.ValueInput.createByString('8'))   # document units: mm
        self.assertAlmostEqual(feature.depth, 0.8)
        self.assertIsInstance(feature.extentOne, fusion.DistanceExtentDefinition)
        self.assertEqual(feature.extentOne.distance.stringValue, '8')

    def test_a_collection_of_profiles_uses_the_first_centroid(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        profiles = core.ObjectCollection.create()
        profiles.add(sketch.profile('a', centroid=(1, 1, 0)))
        profiles.add(sketch.profile('b', centroid=(5, 5, 0)))
        feature = self.extrude(profiles, core.ValueInput.createByReal(1.0))
        self.assertEqual(feature.bodies.count, 1)
        self.assertXYZ(feature.bodies.item(0).location(), (1, 1, 0))
        self.assertIs(feature.profile, profiles)

    def test_an_extrude_without_an_extent_is_refused(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        extrude_input = self.features.extrudeFeatures.createInput(
            sketch.profile('square'), fusion.FeatureOperations.NewBodyFeatureOperation)
        with self.assertRaises(RuntimeError):
            self.features.extrudeFeatures.add(extrude_input)

    def test_deleting_the_extrude_deletes_its_body(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        feature = self.extrude(sketch.profile('square'), core.ValueInput.createByReal(1.0))
        body = feature.bodies.item(0)
        feature.deleteMe()
        self.assertTrue(body._deleted)
        self.assertEqual(self.root.bRepBodies.count, 0)
        self.assertEqual(self.timeline.names(), [])

    def test_the_body_goes_into_the_component_that_owns_the_features(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        sketch = fusion.Sketch('Sketch1', neck)
        extrude_input = neck.features.extrudeFeatures.createInput(
            sketch.profile('square'), fusion.FeatureOperations.NewBodyFeatureOperation)
        extrude_input.setOneSideExtent(fusion.DistanceExtentDefinition.create(core.ValueInput.createByReal(1.0)),
                                       fusion.ExtentDirections.PositiveExtentDirection)
        feature = neck.features.extrudeFeatures.add(extrude_input)
        self.assertIs(feature.bodies.item(0).parentComponent, neck)
        self.assertEqual(neck.bRepBodies.count, 1)
        self.assertEqual(self.root.bRepBodies.count, 0)


class CombineFeatures(Bodies):
    def combine(self, target, tools, operation, keep=False):
        collection = core.ObjectCollection.create()
        for t in tools:
            collection.add(t)
        combine_input = self.features.combineFeatures.createInput(target, collection)
        self.assertEqual(combine_input.operation, fusion.FeatureOperations.JoinFeatureOperation, 'Fusion default')
        self.assertFalse(combine_input.isKeepToolBodies)
        combine_input.operation = operation
        combine_input.isKeepToolBodies = keep
        return self.features.combineFeatures.add(combine_input)

    def test_a_cut_removes_the_tool_bodies(self):
        block = self.root.body('Block')
        tools = [self.root.body('Tool1', origin=(1, 0, 0)), self.root.body('Tool2', origin=(2, 0, 0))]
        feature = self.combine(block, tools, fusion.FeatureOperations.CutFeatureOperation)
        self.assertEqual(feature.objectType, 'adsk::fusion::CombineFeature')
        self.assertTrue(all(t._deleted for t in tools))
        self.assertEqual([b.name for b in self.root.bRepBodies], ['Block'])
        self.assertFalse(block._deleted)
        self.assertIs(feature.targetBody, block)
        self.assertEqual(feature.toolBodies.count, 2)
        self.assertEqual(feature.operation, fusion.FeatureOperations.CutFeatureOperation)
        self.assertFalse(feature.isKeepToolBodies)
        self.assertEqual(feature.bodies.count, 1)
        self.assertIs(feature.bodies.item(0), block)
        self.assertEqual(self.timeline.names(), [feature.name])

    def test_keep_tool_bodies_leaves_them_alone(self):
        block = self.root.body('Block')
        tool = self.root.body('Tool')
        self.combine(block, [tool], fusion.FeatureOperations.JoinFeatureOperation, keep=True)
        self.assertFalse(tool._deleted)
        self.assertEqual(self.root.bRepBodies.count, 2)

    def test_deleting_the_combine_brings_the_tools_back(self):
        block = self.root.body('Block')
        tool = self.root.body('Tool')
        feature = self.combine(block, [tool], fusion.FeatureOperations.CutFeatureOperation)
        feature.deleteMe()
        self.assertFalse(tool._deleted)
        self.assertEqual(self.root.bRepBodies.count, 2)


# ---- components and occurrences ----

class Occurrences(FakeCase):
    def test_add_existing_component_makes_an_occurrence_in_the_timeline(self):
        wheel = fusion.Component('Wheel', self.timeline)
        m = rotation_z(90)
        m.transformBy(translation(10, 0, 0))
        occurrence = self.root.occurrences.addExistingComponent(wheel, m)
        self.assertEqual(occurrence.objectType, 'adsk::fusion::Occurrence')
        self.assertEqual(occurrence.name, 'Wheel:1')
        self.assertIs(occurrence.component, wheel)
        self.assertTrue(occurrence.transform2.isEqualTo(m))
        self.assertIsNot(occurrence.transform2, m, 'the occurrence keeps its own copy')
        self.assertIs(occurrence.timelineObject.entity, occurrence)
        self.assertEqual(self.timeline.names(), ['Wheel:1'])
        self.assertTrue(occurrence.entityToken)
        self.assertEqual(occurrence.attributes.count, 0)
        second = self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        self.assertEqual(second.name, 'Wheel:2')
        self.assertEqual(self.root.occurrences.count, 2)
        self.assertIs(self.root.occurrences.item(1), second)
        self.assertIs(self.root.occurrences.itemByName('Wheel:2'), second)
        found = self.root.allOccurrencesByComponent(wheel)
        self.assertEqual([o.name for o in found], ['Wheel:1', 'Wheel:2'])

    def test_add_component_helper_goes_through_the_collection(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        occurrence = self.root.occurrences.item(0)
        self.assertEqual(occurrence.name, 'Neck:1')
        self.assertIs(occurrence.component, neck)
        self.assertTrue(occurrence.transform2.isEqualTo(core.Matrix3D.create()))
        self.assertEqual(self.root.allOccurrencesByComponent(neck).count, 1)
        self.assertIs(self.root.allOccurrencesByComponent(neck).item(0), occurrence)
        self.assertEqual(self.timeline.names(), ['Neck:1'])
        self.assertEqual(self.root.allOccurrencesByComponent(self.root).count, 0)

    def test_nested_occurrences_are_found_too(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        fret = fusion.Component('Fret', self.timeline)
        inner = neck.occurrences.addExistingComponent(fret, core.Matrix3D.create())
        self.assertEqual(self.root.allOccurrencesByComponent(fret).count, 1)
        self.assertIs(self.root.allOccurrencesByComponent(fret).item(0), inner)
        self.assertEqual(self.root.occurrences.count, 1, 'the inner occurrence belongs to Neck')

    def test_occurrence_delete_me_removes_it_from_the_timeline_and_the_collection(self):
        neck = self.design.add_component('Neck', 'Neck:1')
        occurrence = self.root.occurrences.item(0)
        self.assertTrue(occurrence.deleteMe())
        self.assertTrue(occurrence._deleted)
        self.assertEqual(self.timeline.names(), [])
        self.assertEqual(self.root.occurrences.count, 0)
        self.assertEqual(self.root.allOccurrencesByComponent(neck).count, 0)
        with self.assertRaises(RuntimeError):
            occurrence.deleteMe()

    def test_an_occurrence_after_the_marker_cannot_be_deleted(self):
        self.features.otherFeatures.add('Base')
        wheel = fusion.Component('Wheel', self.timeline)
        occurrence = self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        self.timeline.markerPosition = 1                 # before the occurrence: it does not exist yet
        with self.assertRaisesRegex(RuntimeError, 'findObjectPath'):
            occurrence.deleteMe()
        self.assertFalse(occurrence._deleted)
        self.timeline.markerPosition = 2                 # after it: it does
        self.assertTrue(occurrence.deleteMe())
        self.assertEqual(self.timeline.names(), ['Base'])

    def test_an_occurrence_in_a_collapsed_group_after_the_marker_cannot_be_deleted_either(self):
        self.features.otherFeatures.add('Base')
        wheel = fusion.Component('Wheel', self.timeline)
        first = self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        self.timeline.timelineGroups.add(1, 2)
        self.timeline.markerPosition = 1                 # before the group
        with self.assertRaisesRegex(RuntimeError, 'findObjectPath'):
            first.deleteMe()
        self.timeline.markerPosition = 2                 # after the (collapsed) group
        self.assertTrue(first.deleteMe())

    def test_occurrences_can_be_grouped_with_features_in_the_timeline(self):
        self.features.otherFeatures.add('Base')
        wheel = fusion.Component('Wheel', self.timeline)
        for _ in range(3):
            self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        group = self.timeline.timelineGroups.add(1, 3)
        group.name = 'Wheels'
        self.assertEqual(self.timeline.names(), ['Base', ['Wheel:1', 'Wheel:2', 'Wheel:3']])
        self.assertEqual(self.timeline.markerPosition, 2)
        group.deleteMe(True)
        self.assertEqual(self.timeline.names(), ['Base'])
        self.assertEqual(self.root.occurrences.count, 0)


# ---- sketches ----

class SketchGeometry(FakeCase):
    def setUp(self):
        super().setUp()
        self.sketch = fusion.Sketch('Plate', self.root)

    def test_sketch_axes(self):
        self.assertXYZ(self.sketch.origin, (0, 0, 0))
        self.assertXYZ(self.sketch.xDirection, (1, 0, 0))
        self.assertXYZ(self.sketch.yDirection, (0, 1, 0))
        self.assertIs(self.sketch.parentComponent, self.root)
        self.assertTrue(self.sketch.transform.isEqualTo(core.Matrix3D.create()))

    def test_entities_made_with_just_a_name_still_work(self):
        line, point, circle = self.sketch.line('l'), self.sketch.point('p'), self.sketch.circle('c')
        self.assertXYZ(line.geometry.startPoint, (0, 0, 0))
        self.assertXYZ(line.geometry.endPoint, (1, 0, 0))
        self.assertXYZ(point.geometry, (0, 0, 0))
        self.assertXYZ(circle.geometry.center, (0, 0, 0))
        self.assertEqual(circle.radius, 1.0)
        self.assertEqual(line.objectType, 'adsk::fusion::SketchLine')
        self.assertEqual(line.entityToken, 'Plate/l')

    def test_line_geometry_and_bounding_box(self):
        line = self.sketch.line('l', start=(1, 2), end=(4, 6))
        g = line.geometry
        self.assertIsInstance(g, core.Line3D)
        self.assertXYZ(g.startPoint, (1, 2, 0))
        self.assertXYZ(g.endPoint, (4, 6, 0))
        self.assertXYZ(line.worldGeometry.endPoint, (4, 6, 0), 'sketch space is model space in the fake')
        self.assertXYZ(line.boundingBox.minPoint, (1, 2, 0))
        self.assertXYZ(line.boundingBox.maxPoint, (4, 6, 0))

    def test_point_and_circle_geometry(self):
        point = self.sketch.point('p', at=(3, -1))
        self.assertXYZ(point.geometry, (3, -1, 0))
        self.assertXYZ(point.worldGeometry, (3, -1, 0))
        self.assertXYZ(point.boundingBox.minPoint, (3, -1, 0))
        circle = self.sketch.circle('c', centre=(2, 2), radius=0.5)
        g = circle.geometry
        self.assertXYZ(g.center, (2, 2, 0))
        self.assertEqual(g.radius, 0.5)
        self.assertXYZ(g.normal, (0, 0, 1))
        self.assertXYZ(circle.centerSketchPoint.geometry, (2, 2, 0))
        self.assertXYZ(circle.boundingBox.minPoint, (1.5, 1.5, 0))
        self.assertXYZ(circle.boundingBox.maxPoint, (2.5, 2.5, 0))
        circle.radius = 2.0
        self.assertEqual(circle.geometry.radius, 2.0)
        self.assertXYZ(circle.boundingBox.minPoint, (0, 0, 0))

    def test_copy_moves_the_geometry_by_the_matrix(self):
        line = self.sketch.line('l', start=(0, 0), end=(1, 0))
        circle = self.sketch.circle('c', centre=(1, 0), radius=0.25)
        entities = core.ObjectCollection.create()
        entities.add(line)
        entities.add(circle)
        m = rotation_z(90)
        m.transformBy(translation(10, 0, 0))
        copies = self.sketch.copy(entities, m)
        self.assertIsInstance(copies, core.ObjectCollection)
        self.assertEqual(copies.count, 2)
        new_line, new_circle = copies.item(0), copies.item(1)
        self.assertIsInstance(new_line, fusion.SketchLine)
        self.assertIsInstance(new_circle, fusion.SketchCircle)
        self.assertEqual(new_line.name, 'l copy 1')
        self.assertEqual(new_circle.name, 'c copy 1')
        self.assertXYZ(new_line.geometry.startPoint, (10, 0, 0))
        self.assertXYZ(new_line.geometry.endPoint, (10, 1, 0))
        self.assertXYZ(new_circle.geometry.center, (10, 1, 0))
        self.assertEqual(new_circle.radius, 0.25)
        self.assertXYZ(new_circle.centerSketchPoint.geometry, (10, 1, 0))
        self.assertXYZ(line.geometry.endPoint, (1, 0, 0), 'the original stays put')
        self.assertIs(new_line.parentSketch, self.sketch)
        self.assertNotEqual(new_line.entityToken, line.entityToken)
        self.assertEqual(new_line.attributes.count, 0)
        self.assertEqual(self.sketch.copies, [new_line, new_circle])
        second = self.sketch.copy(entities, translation(1, 1))
        self.assertEqual(second.item(0).name, 'l copy 2')
        self.assertEqual(len(self.sketch.copies), 4)

    def test_copy_with_a_scaling_matrix_scales_a_circle(self):
        circle = self.sketch.circle('c', centre=(1, 0), radius=0.5)
        m = core.Matrix3D.create()
        m.setWithArray([2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 2, 0, 0, 0, 0, 1])
        entities = core.ObjectCollection.create()
        entities.add(circle)
        copy = self.sketch.copy(entities, m).item(0)
        self.assertXYZ(copy.geometry.center, (2, 0, 0))
        self.assertEqual(copy.radius, 1.0)

    def test_copy_refuses_entities_from_another_sketch(self):
        other = fusion.Sketch('Other').line('stray')
        entities = core.ObjectCollection.create()
        entities.add(other)
        with self.assertRaises(RuntimeError):
            self.sketch.copy(entities, core.Matrix3D.create())

    def test_copy_accepts_a_list(self):
        point = self.sketch.point('p', at=(1, 1))
        copies = self.sketch.copy([point], translation(1, 0))
        self.assertXYZ(copies.item(0).geometry, (2, 1, 0))

    def test_scale_scales_about_the_point(self):
        circle = self.sketch.circle('c', centre=(2, 0), radius=0.5)
        line = self.sketch.line('l', start=(1, 0), end=(3, 0))
        self.assertTrue(self.sketch.scale([circle, line], P(1, 0, 0), 2.0))
        self.assertXYZ(circle.geometry.center, (3, 0, 0))
        self.assertEqual(circle.radius, 1.0)
        self.assertXYZ(circle.centerSketchPoint.geometry, (3, 0, 0))
        self.assertXYZ(line.geometry.startPoint, (1, 0, 0))
        self.assertXYZ(line.geometry.endPoint, (5, 0, 0))
        self.assertTrue(self.sketch.scale([self.sketch.point('p', at=(0, 2))], P(0, 0, 0), 0.5))

    def test_scale_refuses_entities_from_another_sketch(self):
        with self.assertRaises(RuntimeError):
            self.sketch.scale([fusion.Sketch('Other').circle('c')], P(0, 0, 0), 2.0)

    def test_a_proxy_sees_the_moved_geometry(self):
        circle = self.sketch.circle('c', centre=(2, 0), radius=0.5)
        proxy = circle.proxy('Neck:1')
        self.sketch.scale([circle], P(0, 0, 0), 2.0)
        self.assertXYZ(proxy.geometry.center, (4, 0, 0))
        self.assertEqual(proxy.radius, 1.0)

    def test_profiles_are_listed_on_the_sketch(self):
        a = self.sketch.profile('a', centroid=(1, 1, 0))
        b = self.sketch.profile('b')
        self.assertEqual(self.sketch.profiles.count, 2)
        self.assertIs(self.sketch.profiles.item(1), b)
        self.assertXYZ(b.areaProperties().centroid, (0, 0, 0))
        self.assertIsNone(a.nativeObject)
        proxy = a.createForAssemblyContext(fusion.Entity('Neck:1', 'Occurrence'))
        self.assertIs(proxy.nativeObject, a)
        self.assertEqual(proxy.entityToken, a.entityToken)

    def test_pattern_constraint_copies_and_sketch_copies_do_not_share_names(self):
        line = self.sketch.line('l')
        constraint = self.sketch.geometricConstraints.addRectangularPattern(
            self.sketch.geometricConstraints.createRectangularPatternInput(
                [line], fusion.PatternDistanceType.SpacingPatternDistanceType))
        copies = self.sketch.copy([line], translation(1, 0))
        names = {constraint.createdEntities[0].name, copies.item(0).name}
        self.assertEqual(len(names), 2)


# ---- tokens and the timeline ----

class EntityTokens(FakeCase):
    def test_find_entity_by_token_finds_live_things_only(self):
        body = self.root.body('Body1')
        sketch = fusion.Sketch('Sketch1', self.root)
        line = sketch.line('l')
        feature = self.features.otherFeatures.add('Fillet1')
        neck = self.design.add_component('Neck', 'Neck:1')
        occurrence = self.root.occurrences.item(0)
        profile = sketch.profile('square')
        edge = fusion.Entity('edge')
        for thing in (body, line, feature, occurrence, profile, edge, neck, sketch):
            self.assertEqual(self.design.findEntityByToken(thing.entityToken), [thing], thing)
        self.assertEqual(self.design.findEntityByToken('no such token'), [])
        body.deleteMe()
        line.deleteMe()
        feature.deleteMe()
        occurrence.deleteMe()
        for thing in (body, line, feature, occurrence):
            self.assertEqual(self.design.findEntityByToken(thing.entityToken), [], thing)

    def test_tokens_are_unique_per_object(self):
        tokens = [self.root.body(f'B{k}').entityToken for k in range(3)]
        tokens += [self.features.otherFeatures.add(f'F{k}').entityToken for k in range(3)]
        tokens += [fusion.Entity(f'E{k}').entityToken for k in range(3)]
        self.assertEqual(len(set(tokens)), len(tokens))

    def test_reset_clears_the_registry(self):
        body = self.root.body('Body1')
        token = body.entityToken
        core.Application.reset()
        design = fusion.Design()
        self.assertEqual(design.findEntityByToken(token), [])
        self.assertEqual(design.findEntityByToken('Sketch1/l'), [])


class TimelineIntegration(Bodies):
    def test_every_new_feature_kind_shows_up_in_the_timeline_in_order(self):
        sketch = fusion.Sketch('Sketch1', self.root)
        block = self.root.body('Block')
        copy_feature = self.features.copyPasteBodies.add(block)
        copy_feature.name = 'Copy 1/2'
        copy = copy_feature.bodies.item(0)
        entities = core.ObjectCollection.create()
        entities.add(copy)
        scale_input = self.features.scaleFeatures.createInput(entities, P(0, 0, 0), core.ValueInput.createByReal(2))
        scale_feature = self.features.scaleFeatures.add(scale_input)
        scale_feature.name = 'Scale 1/2'
        move_feature = self.move([copy], translation(1, 0, 0))
        move_feature.name = 'Move 1/2'
        extrude_input = self.features.extrudeFeatures.createInput(
            sketch.profile('p'), fusion.FeatureOperations.NewBodyFeatureOperation)
        extrude_input.setOneSideExtent(fusion.DistanceExtentDefinition.create(core.ValueInput.createByReal(1)),
                                       fusion.ExtentDirections.PositiveExtentDirection)
        extrude_feature = self.features.extrudeFeatures.add(extrude_input)
        extrude_feature.name = 'Extrude 2/2'
        tools = core.ObjectCollection.create()
        tools.add(extrude_feature.bodies.item(0))
        combine_input = self.features.combineFeatures.createInput(block, tools)
        combine_input.operation = fusion.FeatureOperations.CutFeatureOperation
        combine_feature = self.features.combineFeatures.add(combine_input)
        combine_feature.name = 'Combine'
        wheel = fusion.Component('Wheel', self.timeline)
        self.root.occurrences.addExistingComponent(wheel, core.Matrix3D.create())
        self.assertEqual(self.timeline.names(),
                         ['Copy 1/2', 'Scale 1/2', 'Move 1/2', 'Extrude 2/2', 'Combine', 'Wheel:1'])
        self.assertEqual(self.timeline.markerPosition, 6)
        for feature in (copy_feature, scale_feature, move_feature, extrude_feature, combine_feature):
            self.assertIs(feature.parentComponent, self.root)
            self.assertTrue(feature.entityToken)
            self.assertFalse(feature.isSuppressed)
            self.assertEqual(feature.attributes.count, 0)
            self.assertIsNone(feature.assemblyContext)
        group = self.timeline.timelineGroups.add(0, 4)
        group.name = 'Pattern'
        self.assertEqual(self.timeline.names(),
                         [['Copy 1/2', 'Scale 1/2', 'Move 1/2', 'Extrude 2/2', 'Combine'], 'Wheel:1'])
        self.assertEqual(self.timeline.markerPosition, 2)

    def test_a_feature_made_while_rolled_back_goes_in_at_the_marker(self):
        block = self.root.body('Block')
        self.features.otherFeatures.add('Base')
        self.features.otherFeatures.add('Fillet1')
        self.timeline.markerPosition = 1
        feature = self.move([block], translation(1, 0, 0))
        feature.name = 'Move'
        self.assertEqual(self.timeline.names(), ['Base', 'Move', 'Fillet1'])
        self.assertEqual(self.timeline.markerPosition, 2)

    def test_the_fail_at_hook_works_for_the_new_collections(self):
        block = self.root.body('Block')
        self.features.moveFeatures.fail_at = 1
        self.assertIsNone(self.move([block], translation(1, 0, 0)))
        self.assertEqual(block.location(), (0, 0, 0))
        self.features.copyPasteBodies.fail_at = 1
        self.assertIsNone(self.features.copyPasteBodies.add(block))
        self.features.moveFeatures.reject = 'inputEntities'
        with self.assertRaises(RuntimeError):
            self.features.moveFeatures.createInput2(core.ObjectCollection.create())

    def test_enums(self):
        self.assertEqual(fusion.FeatureOperations.JoinFeatureOperation, 0)
        self.assertEqual(fusion.FeatureOperations.CutFeatureOperation, 1)
        self.assertEqual(fusion.FeatureOperations.IntersectFeatureOperation, 2)
        self.assertEqual(fusion.FeatureOperations.NewBodyFeatureOperation, 3)
        self.assertEqual(fusion.FeatureOperations.NewComponentFeatureOperation, 4)
        self.assertEqual(fusion.ExtentDirections.PositiveExtentDirection, 0)
        self.assertEqual(fusion.ExtentDirections.NegativeExtentDirection, 1)
        self.assertEqual(fusion.ExtentDirections.SymmetricExtentDirection, 2)


if __name__ == '__main__':
    unittest.main()
