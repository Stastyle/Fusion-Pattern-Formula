"""Tests for FormulaPattern/placement.py: matrices as plain lists, vectors from fake entities."""
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'tests', 'fake_adsk'))
sys.path.insert(0, ROOT)

import adsk.core  # noqa: E402  (the fake)
import adsk.fusion  # noqa: E402
from FormulaPattern import placement as P  # noqa: E402


def close(a, b, places=9):
    return all(abs(x - y) < 10 ** -places for x, y in zip(a, b))


class Matrices(unittest.TestCase):
    def test_identity_leaves_a_point_alone(self):
        self.assertEqual(P.apply(P.identity(), (1.0, 2.0, 3.0)), (1.0, 2.0, 3.0))

    def test_translation_moves_a_point(self):
        self.assertEqual(P.apply(P.translation(1, 2, 3), (1.0, 1.0, 1.0)), (2.0, 3.0, 4.0))

    def test_rotation_about_z_turns_x_into_y(self):
        m = P.rotation(90.0, (0, 0, 1))
        self.assertTrue(close(P.apply(m, (1.0, 0.0, 0.0)), (0.0, 1.0, 0.0)))

    def test_rotation_about_an_offset_axis_keeps_the_axis_point(self):
        m = P.rotation(90.0, (0, 0, 1), (1.0, 1.0, 0.0))
        self.assertTrue(close(P.apply(m, (1.0, 1.0, 5.0)), (1.0, 1.0, 5.0)))
        self.assertTrue(close(P.apply(m, (2.0, 1.0, 0.0)), (1.0, 2.0, 0.0)))

    def test_multiply_applies_the_right_matrix_first(self):
        rotate = P.rotation(90.0, (0, 0, 1))
        move = P.translation(10, 0, 0)
        # rotate first, then move: (1,0,0) -> (0,1,0) -> (10,1,0)
        self.assertTrue(close(P.apply(P.multiply(move, rotate), (1.0, 0.0, 0.0)), (10.0, 1.0, 0.0)))
        # move first, then rotate: (1,0,0) -> (11,0,0) -> (0,11,0)
        self.assertTrue(close(P.apply(P.multiply(rotate, move), (1.0, 0.0, 0.0)), (0.0, 11.0, 0.0)))

    def test_apply_vector_ignores_translation(self):
        m = P.multiply(P.translation(5, 5, 5), P.rotation(180.0, (0, 0, 1)))
        self.assertTrue(close(P.apply_vector(m, (1.0, 0.0, 0.0)), (-1.0, 0.0, 0.0)))

    def test_rotation_needs_an_axis_with_length(self):
        with self.assertRaises(P.GeometryError):
            P.rotation(10.0, (0, 0, 0))

    def test_orthonormal_pair_makes_the_second_direction_perpendicular(self):
        u, v = P.orthonormal_pair((2, 0, 0), (1, 1, 0))
        self.assertTrue(close(u, (1.0, 0.0, 0.0)))
        self.assertTrue(close(v, (0.0, 1.0, 0.0)))

    def test_orthonormal_pair_rejects_parallel_directions(self):
        with self.assertRaises(ValueError):
            P.orthonormal_pair((1, 0, 0), (-3, 0, 0))

    def test_round_trip_through_matrix3d(self):
        cells = P.multiply(P.translation(1, 2, 3), P.rotation(30.0, (0, 1, 0)))
        back = P.from_matrix3d(P.to_matrix3d(cells))
        self.assertTrue(close(back, cells))


class Entities(unittest.TestCase):
    def test_direction_of_a_linear_edge_is_its_unit_vector(self):
        edge = adsk.fusion.Entity('edge', 'BRepEdge', start=(0, 0, 0), end=(0, 3, 4))
        self.assertTrue(close(P.direction_of(edge), (0.0, 0.6, 0.8)))

    def test_axis_of_a_construction_axis_gives_origin_and_direction(self):
        axis = adsk.fusion.Entity('Z axis', 'ConstructionAxis', start=(1, 1, 0), end=(1, 1, 2))
        origin, direction = P.axis_of(axis)
        self.assertTrue(close(origin, (1.0, 1.0, 0.0)))
        self.assertTrue(close(direction, (0.0, 0.0, 1.0)))

    def test_direction_of_a_sketch_line_uses_world_geometry(self):
        sketch = adsk.fusion.Sketch('S')
        line = sketch.line('d', start=(0, 0), end=(2, 0))
        self.assertTrue(close(P.direction_of(line), (1.0, 0.0, 0.0)))
        self.assertEqual(P.sketch_direction_of(line), (1.0, 0.0))

    def test_point_of_vertex_sketch_point_and_construction_point(self):
        vertex = adsk.fusion.Entity('corner', 'BRepVertex', start=(1, 2, 3))
        self.assertTrue(close(P.point_of(vertex), (1.0, 2.0, 3.0)))
        sketch = adsk.fusion.Sketch('S')
        self.assertTrue(close(P.point_of(sketch.point('p', at=(4, 5))), (4.0, 5.0, 0.0)))
        self.assertEqual(P.sketch_point_of(sketch.point('q', at=(7, 8))), (7.0, 8.0))
        self.assertEqual(P.sketch_point_of(sketch.circle('c', centre=(1, 1), radius=2)), (1.0, 1.0))

    def test_a_point_is_not_a_direction(self):
        with self.assertRaises(P.GeometryError):
            P.direction_of(adsk.fusion.Entity('corner', 'BRepVertex', start=(1, 2, 3)))


if __name__ == '__main__':
    unittest.main()
