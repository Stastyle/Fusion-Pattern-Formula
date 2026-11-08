"""Geometry for the transform engine: vectors from Fusion entities, 4×4 matrices as plain lists.

Matrices are 16 floats, row-major, the layout of ``adsk.core.Matrix3D.asArray()``. They
are composed here with ordinary Python arithmetic and only handed to Fusion at the very
end (``to_matrix3d``), so nothing depends on the order semantics of ``Matrix3D.transformBy``.
Lengths are in centimetres, Fusion's internal unit; angles are in degrees unless a name
says otherwise.
"""
import math

import adsk.core

EPSILON = 1e-9


class GeometryError(ValueError):
    """An entity cannot give the vector or point that was asked of it; the message says why."""


# ---------------------------------------------------------------------------
# Small vector helpers (tuples of three floats)
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _length(v):
    return math.sqrt(_dot(v, v))


def _unit(v, what='direction'):
    n = _length(v)
    if n < EPSILON:
        raise GeometryError(f'The {what} has no length')
    return (v[0] / n, v[1] / n, v[2] / n)


def _xyz(point_or_vector):
    return (float(point_or_vector.x), float(point_or_vector.y), float(point_or_vector.z))


def _type_name(entity) -> str:
    try:
        return entity.objectType.split('::')[-1]
    except Exception:
        return type(entity).__name__


# ---------------------------------------------------------------------------
# From Fusion entities to vectors and points
# ---------------------------------------------------------------------------

def _line_of(entity):
    """(point, direction) of a linear edge, construction axis or sketch line, in model space."""
    kind = _type_name(entity)
    geometry = None
    try:
        if kind == 'SketchLine':
            geometry = entity.worldGeometry
        else:
            geometry = entity.geometry
    except Exception:
        geometry = None
    if geometry is None:
        raise GeometryError(f'{kind} has no line geometry')
    if hasattr(geometry, 'direction') and hasattr(geometry, 'origin'):        # InfiniteLine3D
        return _xyz(geometry.origin), _unit(_xyz(geometry.direction), kind)
    if hasattr(geometry, 'startPoint') and hasattr(geometry, 'endPoint'):     # Line3D
        start, end = _xyz(geometry.startPoint), _xyz(geometry.endPoint)
        return start, _unit(_sub(end, start), kind)
    raise GeometryError(f'{kind} is not a straight line; pick a linear edge, a construction axis or a sketch line')


def direction_of(entity):
    """Unit vector (x, y, z) of a linear BRepEdge, ConstructionAxis or SketchLine."""
    return _line_of(entity)[1]


def axis_of(entity):
    """((ox, oy, oz), (dx, dy, dz)): a point on the entity's line and its unit direction."""
    return _line_of(entity)


def point_of(entity):
    """(x, y, z) of a BRepVertex, SketchPoint (world) or ConstructionPoint."""
    kind = _type_name(entity)
    for attr in (('worldGeometry',) if kind == 'SketchPoint' else ()) + ('geometry',):
        try:
            geometry = getattr(entity, attr)
        except Exception:
            continue
        if geometry is not None and hasattr(geometry, 'x'):
            return _xyz(geometry)
    raise GeometryError(f'{kind} is not a point; pick a vertex, a sketch point or a construction point')


def sketch_direction_of(line):
    """Unit vector (x, y) of a sketch line in its sketch's own coordinates."""
    try:
        geometry = line.geometry
        start, end = _xyz(geometry.startPoint), _xyz(geometry.endPoint)
    except Exception:
        raise GeometryError(f'{_type_name(line)} is not a sketch line') from None
    d = _unit(_sub(end, start), 'sketch line')
    return (d[0], d[1])


def sketch_point_of(point):
    """(x, y) of a sketch point (or a circle's centre) in sketch coordinates."""
    try:
        geometry = point.geometry
        if hasattr(geometry, 'center'):          # a circle: use its centre
            geometry = geometry.center
        return (float(geometry.x), float(geometry.y))
    except Exception:
        raise GeometryError(f'{_type_name(point)} is not a sketch point') from None


def orthonormal_pair(u, v):
    """Unit u and the part of v perpendicular to u, as unit vectors (Gram-Schmidt).
    Raises ValueError('parallel') when v has no component perpendicular to u."""
    u = _unit(u, 'first direction')
    v = _unit(v, 'second direction')
    along = _dot(v, u)
    perpendicular = (v[0] - along * u[0], v[1] - along * u[1], v[2] - along * u[2])
    if _length(perpendicular) < 1e-9:
        raise ValueError('parallel')
    return u, _unit(perpendicular)


# ---------------------------------------------------------------------------
# 4×4 matrices as 16-element lists (row-major)
# ---------------------------------------------------------------------------

def identity():
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def translation(dx, dy, dz):
    m = identity()
    m[3], m[7], m[11] = float(dx), float(dy), float(dz)
    return m


def rotation(angle_deg, axis_dir, axis_point=(0.0, 0.0, 0.0)):
    """Rotation by ``angle_deg`` about the axis through ``axis_point`` along ``axis_dir``
    (right-hand rule)."""
    x, y, z = _unit(axis_dir, 'rotation axis')
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    t = 1.0 - c
    r = [t * x * x + c, t * x * y - s * z, t * x * z + s * y, 0.0,
         t * x * y + s * z, t * y * y + c, t * y * z - s * x, 0.0,
         t * x * z - s * y, t * y * z + s * x, t * z * z + c, 0.0,
         0.0, 0.0, 0.0, 1.0]
    px, py, pz = axis_point
    # Rotate about a point: translate it to the origin, rotate, translate back.
    return multiply(translation(px, py, pz), multiply(r, translation(-px, -py, -pz)))


def multiply(a, b):
    """a · b: the transform that applies b first, then a."""
    out = [0.0] * 16
    for row in range(4):
        for col in range(4):
            out[row * 4 + col] = sum(a[row * 4 + k] * b[k * 4 + col] for k in range(4))
    return out


def apply(cells, point):
    """``point`` (x, y, z) transformed by ``cells``."""
    x, y, z = point
    return (cells[0] * x + cells[1] * y + cells[2] * z + cells[3],
            cells[4] * x + cells[5] * y + cells[6] * z + cells[7],
            cells[8] * x + cells[9] * y + cells[10] * z + cells[11])


def apply_vector(cells, vector):
    """``vector`` (x, y, z) transformed by the rotation part of ``cells`` (no translation)."""
    x, y, z = vector
    return (cells[0] * x + cells[1] * y + cells[2] * z,
            cells[4] * x + cells[5] * y + cells[6] * z,
            cells[8] * x + cells[9] * y + cells[10] * z)


def to_matrix3d(cells):
    m = adsk.core.Matrix3D.create()
    m.setWithArray([float(c) for c in cells])
    return m


def from_matrix3d(matrix):
    return [float(c) for c in matrix.asArray()]
