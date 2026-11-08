"""Fake adsk.fusion — see package docstring."""
import math
import re

from . import core


class PatternDistanceType:
    ExtentPatternDistanceType = 0
    SpacingPatternDistanceType = 1


class DistanceUnits:
    MillimeterDistanceUnits = 0
    CentimeterDistanceUnits = 1
    MeterDistanceUnits = 2
    InchDistanceUnits = 3
    FootDistanceUnits = 4


class PatternComputeOptions:
    OptimizedPatternCompute = 0
    IdenticalPatternCompute = 1
    AdjustPatternCompute = 2


class FeatureOperations:
    JoinFeatureOperation = 0
    CutFeatureOperation = 1
    IntersectFeatureOperation = 2
    NewBodyFeatureOperation = 3
    NewComponentFeatureOperation = 4


class ExtentDirections:
    PositiveExtentDirection = 0
    NegativeExtentDirection = 1
    SymmetricExtentDirection = 2


class PointContainment:
    PointInsidePointContainment = 0
    PointOnPointContainment = 1
    PointOutsidePointContainment = 2
    UnknownPointContainment = 3


class Parameter(core.Base):
    def __init__(self, name, value, unit, expression=''):
        self.name, self.value, self.unit, self.expression = name, value, unit, expression


class UserParameters(core.Base):
    def __init__(self):
        self._items = []

    def add(self, name, value, unit, expression=''):
        p = Parameter(name, value, unit, expression)
        self._items.append(p)
        return p

    def __iter__(self):
        return iter(self._items)

    @property
    def count(self):
        return len(self._items)


# ---- entity tokens ----
#
# Everything that has an entityToken registers it here, so Design.findEntityByToken() can
# hand the object back (deleted objects drop out). Application.reset() clears the registry.

_tokens = {}      # entityToken -> objects carrying it
_counters = {}    # token prefix -> last number handed out


def _token(obj, token=None, prefix='entity'):
    if token is None:
        _counters[prefix] = _counters.get(prefix, 0) + 1
        token = f'{prefix}/{_counters[prefix]}'
    obj.entityToken = token
    _tokens.setdefault(token, []).append(obj)
    return token


def _reset_tokens():
    _tokens.clear()
    _counters.clear()


core._reset_hooks.append(_reset_tokens)


# ---- ValueInput in internal units ----

_NUMBER = re.compile(r'\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*')


def _measure(value_input, table, default_unit, argument):
    """A ValueInput as a float in internal units: a real as given, a string parsed with its
    unit (``default_unit`` when it has none)."""
    if value_input.realValue is not None:
        return float(value_input.realValue)
    m = _NUMBER.fullmatch(value_input.stringValue or '')
    unit = (m.group(2) or default_unit) if m else None
    if m is None or unit not in table:
        raise RuntimeError(f'3 : invalid argument {argument}')
    return float(m.group(1)) * table[unit]


def _length_cm(value_input, component, argument='distance'):
    """Centimetres; a unitless string is in the design's default length units, as in Fusion."""
    design = getattr(component, '_design', None)
    unit = design.unitsManager.defaultLengthUnits if design is not None else 'cm'
    return _measure(value_input, core._LENGTH_TO_CM, unit, argument)


def _angle_rad(value_input, argument='angle'):
    return _measure(value_input, core._ANGLE_TO_RAD, 'deg', argument)


def _factor(value_input, argument='scaleFactor'):
    return _measure(value_input, {'': 1.0}, '', argument)


def _point_of(point):
    """(x, y, z) of a Point3D or of an entity whose geometry is a point."""
    if isinstance(point, core.Point3D):
        return (point.x, point.y, point.z)
    g = getattr(point, 'worldGeometry', None)
    if g is None:
        g = getattr(point, 'geometry', None)
    if isinstance(g, core.Point3D):
        return (g.x, g.y, g.z)
    raise RuntimeError('3 : invalid argument point')


def _axis_of(entity):
    """(origin Point3D, direction Vector3D) of a linear entity."""
    g = getattr(entity, 'worldGeometry', None)
    if g is None:
        g = getattr(entity, 'geometry', None)
    if isinstance(g, core.Line3D):
        return g.startPoint, g.startPoint.vectorTo(g.endPoint)
    if isinstance(g, core.InfiniteLine3D):
        return g.origin, g.direction
    raise RuntimeError('3 : invalid argument axisEntity')


# ---- timeline ----

class TimelineObject(core.Base):
    """A feature's row in the timeline."""

    def __init__(self, timeline, entity):
        self._timeline = timeline
        self.entity = entity
        self.parentGroup = None

    @property
    def index(self):
        # The reference says -1 for a feature inside a collapsed group (and for an expanded
        # group); Fusion 2026-11-06 raised instead. The fake raises too.
        index = self._timeline._index_of(self)
        if index < 0:
            raise RuntimeError('2 : InternalValidationError : res >= 0')
        return index

    @property
    def isRolledBack(self):
        index = self._timeline._index_of(self)
        return index >= 0 and index >= self._timeline.markerPosition

    def rollTo(self, rollBefore):
        """Marker to just before (True) or just after (False) this row."""
        self._timeline.markerPosition = self.index + (0 if rollBefore else 1)
        return True


class TimelineGroup(TimelineObject):
    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, TimelineGroup) else None

    def __init__(self, timeline, members, start, end):
        super().__init__(timeline, None)
        self._members = members
        self.start, self.end = start, end     # the indices the group was created with
        self.name = ''
        self.isCollapsed = True
        for m in members:
            m.parentGroup = self

    @property
    def count(self):
        return len(self._members)

    def item(self, index):
        return self._members[index]

    def __iter__(self):
        return iter(self._members)

    def deleteMe(self, deleteGroupAndContents):
        if deleteGroupAndContents:
            for m in list(self._members):
                m.entity.deleteMe()
        else:
            for m in self._members:
                m.parentGroup = None
            self._members = []
        if self in self._timeline.timelineGroups._groups:
            self._timeline.timelineGroups._groups.remove(self)
        return True


class TimelineGroups(core.Base):
    def __init__(self, timeline):
        self._timeline = timeline
        self._groups = []

    def add(self, startIndex, endIndex):
        if endIndex <= startIndex:
            raise ValueError('a timeline group needs at least two items')
        members = self._timeline._visible()[startIndex:endIndex + 1]
        if len(members) != endIndex - startIndex + 1 or any(isinstance(m, TimelineGroup) for m in members):
            raise RuntimeError('3 : invalid argument endIndex')
        g = TimelineGroup(self._timeline, members, startIndex, endIndex)
        self._groups.append(g)
        # The new group is collapsed: its rows became one, and a marker past them moves up.
        marker = self._timeline._marker
        if marker > startIndex:
            self._timeline._marker = max(startIndex + 1, marker - (len(members) - 1))
        return g

    @property
    def count(self):
        return len(self._groups)

    def item(self, index):
        return self._groups[index]


class Timeline(core.Base):
    """Features in creation order. A collapsed group is one row, as in Fusion, so a feature
    inside one reports index -1 and the marker counts the group as a single step."""

    def __init__(self):
        self._rows = []            # TimelineObject per feature
        self._marker = 0           # in visible rows
        self.timelineGroups = TimelineGroups(self)

    def _visible(self):
        rows, seen = [], set()
        for r in self._rows:
            g = r.parentGroup
            if g is not None and g.isCollapsed:
                if id(g) not in seen:
                    seen.add(id(g))
                    rows.append(g)
            else:
                rows.append(r)
        return rows

    def _index_of(self, obj):
        visible = self._visible()
        return visible.index(obj) if obj in visible else -1

    @property
    def count(self):
        return len(self._visible())

    def item(self, index):
        return self._visible()[index]

    @property
    def markerPosition(self):
        return self._marker

    @markerPosition.setter
    def markerPosition(self, value):
        if not 0 <= value <= self.count:
            raise RuntimeError('3 : invalid argument value')
        self._marker = value

    def moveToEnd(self):
        self._marker = self.count
        return True

    def _insert(self, feature):
        """A feature (or occurrence) created while rolled back goes in at the marker, which
        then moves past it."""
        row = TimelineObject(self, feature)
        visible = self._visible()
        if self._marker >= len(visible):
            self._rows.append(row)
        else:
            at = visible[self._marker]
            first = at._members[0] if isinstance(at, TimelineGroup) else at
            self._rows.insert(self._rows.index(first), row)
        self._marker += 1
        return row

    def _remove(self, row):
        group = row.parentGroup
        position = group.index if group is not None and group.isCollapsed else row.index
        before = self.count
        self._rows.remove(row)
        if group is not None:
            group._members.remove(row)
            if not group._members and group in self.timelineGroups._groups:
                self.timelineGroups._groups.remove(group)
        if position < self._marker:
            self._marker -= before - self.count

    def names(self):
        """Test helper: the visible rows, a collapsed group as [member names]."""
        return [[m.entity.name for m in r] if isinstance(r, TimelineGroup) else r.entity.name
                for r in self._visible()]


# ---- features ----

def _proxy(entity, occurrence):
    """``entity`` as seen through ``occurrence``: what Fusion hands out for geometry in a
    component when the root component is active (createForAssemblyContext)."""
    p = type(entity).__new__(type(entity))
    p.__dict__.update(entity.__dict__)
    p.nativeObject = entity
    p.assemblyContext = occurrence
    return p


class Feature(core.Base):
    _type = None    # objectType suffix; None = the collection's class name without its s

    def __init__(self, collection, feature_input, name=''):
        self._collection = collection
        self.input = feature_input
        self.name = name
        self.attributes = core.Attributes(self)
        self.isSuppressed = False
        self.timelineObject = None
        self.objectType = 'adsk::fusion::' + (self._type or type(collection).__name__[:-1])
        _token(self, prefix='feature')
        self.parentComponent = collection._component
        self.assemblyContext = None
        self.nativeObject = None
        self._bodies = []          # bodies the feature made (or works on)
        self._deleted = False

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    def _require_rolled_back(self):
        # API reference: "To use this property, you need to position the timeline marker to
        # immediately before this feature." Seen in Fusion 2026-11-06: read without that, the
        # inputs came back empty.
        tl = self.timelineObject
        if tl is None:
            return
        group = tl.parentGroup
        position = group.index if group is not None and group.isCollapsed else tl.index
        if tl._timeline.markerPosition > position:
            raise RuntimeError('2 : InternalValidationError : roll the timeline to before this feature first')

    @property
    def inputEntities(self):
        self._require_rolled_back()
        return self.input.inputEntities

    @property
    def directionOneEntity(self):
        self._require_rolled_back()
        return getattr(self.input, 'directionOneEntity', None)

    @property
    def axis(self):
        self._require_rolled_back()
        return getattr(self.input, 'axis', None)

    @property
    def bodies(self):
        return BRepBodies(items=list(self._bodies))

    def _undo(self):
        """What deleting the feature does to the model. Nothing for a pattern in the fake."""

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._undo()
        self._deleted = True
        self._collection.created.remove(self)
        self._collection._timeline._remove(self.timelineObject)
        return True


class _FeatureCollection(core.Base):
    """Common part of every feature collection: what was created, the timeline insertion
    and the test hooks."""

    def __init__(self, timeline, component=None):
        self._timeline = timeline
        self._component = component
        self.created = []
        self.fail_at = None   # test hook: make add() return None for this feature number
        self.reject = None    # test hook: createInput raises "3 : invalid argument <reject>"

    def _reject(self):
        if self.reject:
            raise RuntimeError(f'3 : invalid argument {self.reject}')

    def _failing(self):
        return self.fail_at is not None and len(self.created) + 1 == self.fail_at

    def _insert(self, feature):
        feature.timelineObject = self._timeline._insert(feature)
        self.created.append(feature)
        return feature

    @property
    def count(self):
        return len(self.created)

    def item(self, index):
        return self.created[index]

    def itemByName(self, name):
        return next((f for f in self.created if f.name == name), None)

    def __iter__(self):
        return iter(self.created)

    def __len__(self):
        return len(self.created)


class RectangularPatternFeatureInput(core.Base):
    def __init__(self, entities, direction, quantity, distance, distance_type):
        self.inputEntities = entities
        self.directionOneEntity = direction
        self.quantityOne = quantity
        self.distanceOne = distance
        self.patternDistanceType = distance_type
        self.patternComputeOption = PatternComputeOptions.OptimizedPatternCompute
        self.isSymmetricInDirectionOne = False


class CircularPatternFeatureInput(core.Base):
    def __init__(self, entities, axis):
        self.inputEntities = entities
        self.axis = axis
        self.quantity = None
        self.totalAngle = None
        self.isSymmetric = False
        self.patternComputeOption = PatternComputeOptions.OptimizedPatternCompute


class _PatternFeatures(_FeatureCollection):
    def add(self, pattern_input):
        if self._failing():
            return None
        return self._insert(Feature(self, pattern_input))


class RectangularPatternFeatures(_PatternFeatures):
    def createInput(self, inputEntities, directionOneEntity, quantityOne, distanceOne, patternDistanceType):
        self._reject()
        return RectangularPatternFeatureInput(inputEntities, directionOneEntity, quantityOne, distanceOne,
                                             patternDistanceType)


class CircularPatternFeatures(_PatternFeatures):
    def createInput(self, inputEntities, axis):
        self._reject()
        return CircularPatternFeatureInput(inputEntities, axis)


class OtherFeatures(_FeatureCollection):
    """Test helper: any other feature (a fillet, say) to put before or after a pattern."""

    def add(self, name):
        feature = Feature(self, None, name)
        feature.objectType = 'adsk::fusion::FilletFeature'
        return self._insert(feature)


# -- copy / paste --

class CopyPasteBody(Feature):
    _type = 'CopyPasteBody'

    def __init__(self, collection, sources, copies):
        super().__init__(collection, None)
        self.sourceBody = core.ObjectCollection.createWithArray(sources)
        self._bodies = copies

    def _undo(self):
        for body in self._bodies:
            if not body._deleted:
                body.deleteMe()


class CopyPasteBodies(_FeatureCollection):
    def add(self, sourceBody):
        """Pastes a body (or a collection of bodies) into this collection's component, at the
        same place as the original."""
        sources = list(sourceBody) if isinstance(sourceBody, core.ObjectCollection) else [sourceBody]
        for body in sources:
            if not isinstance(body, BRepBody):
                raise RuntimeError('3 : invalid argument sourceBody')
        if self._failing():
            return None
        copies = [body._paste(self._component or body.parentComponent) for body in sources]
        return self._insert(CopyPasteBody(self, sources, copies))


# -- move --

class MoveFeatureInput(core.Base):
    def __init__(self, entities, component):
        self.inputEntities = entities
        self.transform = None          # the Matrix3D the move applies; None until defined
        self.targetBaseFeature = None
        self._component = component

    def defineAsFreeMove(self, transform):
        # The matrix must be orthogonal: unit, perpendicular axes; no scaling. Fusion rejects
        # anything else with "3 : invalid argument transform".
        c = transform._cells
        columns = [(c[j], c[4 + j], c[8 + j]) for j in range(3)]
        for column in columns:
            if abs(math.sqrt(sum(v * v for v in column)) - 1.0) > 1e-6:
                raise RuntimeError('3 : invalid argument transform')
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(sum(a * b for a, b in zip(columns[i], columns[j]))) > 1e-6:
                    raise RuntimeError('3 : invalid argument transform')
        self.transform = transform.copy()
        return True

    def defineAsRotate(self, axisEntity, angle):
        origin, direction = _axis_of(axisEntity)
        m = core.Matrix3D.create()
        m.setToRotation(_angle_rad(angle), direction, origin)
        self.transform = m
        return True

    def defineAsTranslateXYZ(self, xDistance, yDistance, zDistance, isDesignSpace):
        m = core.Matrix3D.create()
        m.translation = core.Vector3D.create(_length_cm(xDistance, self._component, 'xDistance'),
                                             _length_cm(yDistance, self._component, 'yDistance'),
                                             _length_cm(zDistance, self._component, 'zDistance'))
        self.transform = m
        return True


class MoveFeature(Feature):
    _type = 'MoveFeature'

    def __init__(self, collection, move_input):
        super().__init__(collection, move_input)
        self.transform = move_input.transform.copy()
        self._moved = list(move_input.inputEntities)
        self._bodies = [e for e in self._moved if isinstance(e, BRepBody)]

    def _undo(self):
        inverse = self.transform.copy()
        inverse.invert()
        for entity in self._moved:
            entity._apply(inverse._cells)


class MoveFeatures(_FeatureCollection):
    def createInput2(self, inputEntities):
        self._reject()
        return MoveFeatureInput(inputEntities, self._component)

    def add(self, move_input):
        """Applies the move to every body (or occurrence) in inputEntities."""
        if move_input.transform is None:
            raise RuntimeError('3 : invalid argument input')
        for entity in move_input.inputEntities:
            if not hasattr(entity, '_apply'):
                raise RuntimeError('3 : invalid argument inputEntities')
        if self._failing():
            return None
        for entity in move_input.inputEntities:
            entity._apply(move_input.transform._cells)
        return self._insert(MoveFeature(self, move_input))


# -- scale --

class ScaleFeatureInput(core.Base):
    def __init__(self, entities, point, scale_factor):
        self.inputEntities = entities
        self.point = point
        self.scaleFactor = scale_factor
        self.isUniform = True
        self.xScale = self.yScale = self.zScale = None
        self.targetBaseFeature = None

    def setToNonUniform(self, xScale, yScale, zScale):
        self.isUniform = False
        self.xScale, self.yScale, self.zScale = xScale, yScale, zScale
        return True


class ScaleFeature(Feature):
    _type = 'ScaleFeature'

    def __init__(self, collection, scale_input, factor, centre):
        super().__init__(collection, scale_input)
        self.point = scale_input.point
        self.isUniform = scale_input.isUniform
        self.scaleFactor = factor          # plain float in the fake (a ModelParameter in Fusion)
        self._centre = centre
        self._bodies = list(scale_input.inputEntities)

    def _undo(self):
        for body in self._bodies:
            body._scale_about(self._centre, 1.0 / self.scaleFactor)


class ScaleFeatures(_FeatureCollection):
    def createInput(self, inputEntities, point, scaleFactor):
        self._reject()
        return ScaleFeatureInput(inputEntities, point, scaleFactor)

    def add(self, scale_input):
        """Uniform scaling of bodies about the point: multiplies each body's ``_scale`` and
        moves it away from (or towards) the point."""
        if not scale_input.isUniform:
            raise RuntimeError('3 : invalid argument input')      # the fake does uniform scaling only
        factor = _factor(scale_input.scaleFactor)
        centre = _point_of(scale_input.point)
        for entity in scale_input.inputEntities:
            if not isinstance(entity, BRepBody):
                raise RuntimeError('3 : invalid argument inputEntities')
        if self._failing():
            return None
        for body in scale_input.inputEntities:
            body._scale_about(centre, factor)
        return self._insert(ScaleFeature(self, scale_input, factor, centre))


# -- extrude --

class ExtentDefinition(core.Base):
    pass


class DistanceExtentDefinition(ExtentDefinition):
    def __init__(self, distance):
        self.distance = distance      # the ValueInput as given

    @staticmethod
    def create(distance):
        return DistanceExtentDefinition(distance)


class ExtrudeFeatureInput(core.Base):
    def __init__(self, profile, operation):
        self.profile = profile          # a Profile or an ObjectCollection of profiles
        self.operation = operation
        self.extentOne = None
        self.isSolid = True
        self.participantBodies = []
        self._direction = None

    def setOneSideExtent(self, extent, direction, taperAngle=None):
        if not isinstance(extent, DistanceExtentDefinition):
            raise RuntimeError('3 : invalid argument extent')      # the fake knows distances only
        self.extentOne, self._direction = extent, direction
        return True


class ExtrudeFeature(Feature):
    _type = 'ExtrudeFeature'

    def __init__(self, collection, extrude_input, depth, bodies):
        super().__init__(collection, extrude_input)
        self.profile = extrude_input.profile
        self.operation = extrude_input.operation
        self.extentOne = extrude_input.extentOne
        self.depth = depth          # test helper: signed centimetres (negative = NegativeExtentDirection)
        self._bodies = bodies

    def _undo(self):
        for body in self._bodies:
            if not body._deleted:
                body.deleteMe()


class ExtrudeFeatures(_FeatureCollection):
    def createInput(self, profile, operation):
        self._reject()
        return ExtrudeFeatureInput(profile, operation)

    def add(self, extrude_input):
        """A new-body extrude makes one body at the first profile's centroid; the signed depth
        is recorded on the feature. Join / Cut / Intersect make no body in the fake."""
        if extrude_input.extentOne is None:
            raise RuntimeError('3 : invalid argument input')
        distance = _length_cm(extrude_input.extentOne.distance, self._component)
        if extrude_input._direction == ExtentDirections.NegativeExtentDirection:
            distance = -distance
        profile = extrude_input.profile
        profiles = list(profile) if isinstance(profile, core.ObjectCollection) else [profile]
        if not profiles or not all(isinstance(p, Profile) for p in profiles):
            raise RuntimeError('3 : invalid argument profile')
        if self._failing():
            return None
        bodies = []
        if extrude_input.operation in (FeatureOperations.NewBodyFeatureOperation,
                                       FeatureOperations.NewComponentFeatureOperation):
            centroid = profiles[0].areaProperties().centroid
            bodies = [self._component._new_body(origin=(centroid.x, centroid.y, centroid.z))]
        return self._insert(ExtrudeFeature(self, extrude_input, distance, bodies))


# -- combine --

class CombineFeatureInput(core.Base):
    def __init__(self, target, tools):
        self.targetBody = target
        self.toolBodies = tools
        self.operation = FeatureOperations.JoinFeatureOperation
        self.isKeepToolBodies = False
        self.isNewComponent = False
        self.targetBaseFeature = None


class CombineFeature(Feature):
    _type = 'CombineFeature'

    def __init__(self, collection, combine_input, removed):
        super().__init__(collection, combine_input)
        self.targetBody = combine_input.targetBody
        self.toolBodies = combine_input.toolBodies
        self.operation = combine_input.operation
        self.isKeepToolBodies = combine_input.isKeepToolBodies
        self._removed = removed
        self._bodies = [self.targetBody]

    def _undo(self):
        for body in self._removed:
            body._restore()


class CombineFeatures(_FeatureCollection):
    def createInput(self, targetBody, toolBodies):
        self._reject()
        return CombineFeatureInput(targetBody, toolBodies)

    def add(self, combine_input):
        """The tool bodies are consumed (deleted) unless isKeepToolBodies."""
        tools = list(combine_input.toolBodies)
        if not isinstance(combine_input.targetBody, BRepBody) or not all(isinstance(t, BRepBody) for t in tools):
            raise RuntimeError('3 : invalid argument input')
        if self._failing():
            return None
        removed = []
        if not combine_input.isKeepToolBodies:
            for tool in tools:
                tool.deleteMe()
                removed.append(tool)
        return self._insert(CombineFeature(self, combine_input, removed))


class Features(core.Base):
    def __init__(self, timeline, component=None):
        self.rectangularPatternFeatures = RectangularPatternFeatures(timeline, component)
        self.circularPatternFeatures = CircularPatternFeatures(timeline, component)
        self.otherFeatures = OtherFeatures(timeline, component)
        self.copyPasteBodies = CopyPasteBodies(timeline, component)
        self.moveFeatures = MoveFeatures(timeline, component)
        self.scaleFeatures = ScaleFeatures(timeline, component)
        self.extrudeFeatures = ExtrudeFeatures(timeline, component)
        self.combineFeatures = CombineFeatures(timeline, component)


# ---- bodies ----

class PhysicalProperties(core.Base):
    def __init__(self, center_of_mass, volume):
        self.centerOfMass = center_of_mass
        self.volume = volume
        self.mass = volume
        self.density = 1.0
        self.area = 6.0 * volume ** (2.0 / 3.0)


class BRepBody(core.Base):
    """A body that remembers where it has been moved and scaled. ``_transform`` (16 cells,
    identity to start with) is every Move applied so far, ``_scale`` every Scale. Proxies
    made with createForAssemblyContext share the state, so they stay live views."""

    def __init__(self, name, component, origin=(0.0, 0.0, 0.0)):
        self.name = name
        self.parentComponent = component
        self.objectType = 'adsk::fusion::BRepBody'
        self.attributes = core.Attributes(self)
        self.assemblyContext = None
        self.nativeObject = None
        self.isVisible = True
        self.isLightBulbOn = True
        self.isSolid = True
        self._origin = tuple(float(v) for v in origin)
        self._state = {'transform': core._identity(), 'scale': 1.0, 'deleted': False}
        _token(self, prefix='body')

    @property
    def _transform(self):
        return self._state['transform']

    @_transform.setter
    def _transform(self, cells):
        self._state['transform'] = list(cells)

    @property
    def _scale(self):
        return self._state['scale']

    @_scale.setter
    def _scale(self, value):
        self._state['scale'] = float(value)

    @property
    def _deleted(self):
        return self._state['deleted']

    @_deleted.setter
    def _deleted(self, value):
        self._state['deleted'] = bool(value)

    def location(self):
        """Test helper: where the body's origin is now, as an (x, y, z) tuple."""
        return core._apply_point(self._transform, *self._origin)

    @property
    def boundingBox(self):
        # The fake body is a unit cube around its origin, scaled by _scale.
        half = 0.5 * self._scale
        x, y, z = self.location()
        return core.BoundingBox3D.create(core.Point3D.create(x - half, y - half, z - half),
                                         core.Point3D.create(x + half, y + half, z + half))

    def pointContainment(self, point):
        """Inside / on / outside the unit cube the fake body is."""
        half = 0.5 * self._scale
        x, y, z = self.location()
        distances = [abs(point.x - x), abs(point.y - y), abs(point.z - z)]
        if any(d > half + 1e-9 for d in distances):
            return PointContainment.PointOutsidePointContainment
        if any(abs(d - half) <= 1e-9 for d in distances):
            return PointContainment.PointOnPointContainment
        return PointContainment.PointInsidePointContainment

    @property
    def physicalProperties(self):
        return PhysicalProperties(core.Point3D.create(*self.location()), self._scale ** 3)

    def getPhysicalProperties(self, accuracy=0):
        return self.physicalProperties

    @property
    def volume(self):
        return self._scale ** 3

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._deleted = True
        native = self.nativeObject or self
        bodies = self.parentComponent.bRepBodies._items if self.parentComponent is not None else []
        if native in bodies:
            bodies.remove(native)
        return True

    def _restore(self):
        """Undo of a deletion (a Combine that consumed the body is deleted)."""
        self._deleted = False
        native = self.nativeObject or self
        if self.parentComponent is not None and native not in self.parentComponent.bRepBodies._items:
            self.parentComponent.bRepBodies._items.append(native)

    def _apply(self, cells):
        """Move: new transform = cells · old."""
        self._transform = core._multiply(cells, self._transform)

    def _scale_about(self, centre, factor):
        """Uniform scale about ``centre`` (x, y, z): the body grows and its origin moves
        away from the centre, as a Scale feature does."""
        self._scale = self._scale * factor
        location = self.location()
        self._apply(core._translation(*((factor - 1.0) * (l - c) for l, c in zip(location, centre))))

    def _paste(self, component):
        copy = component._new_body(origin=self._origin)
        copy._transform = self._transform
        copy._scale = self._scale
        return copy


class BRepBodies(core.Base):
    def __init__(self, component=None, items=None):
        self._component = component
        self._items = items if items is not None else []

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def itemByName(self, name):
        return next((b for b in self._items if b.name == name), None)

    def asArray(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


# ---- components and occurrences ----

class Occurrence(core.Base):
    def __init__(self, name, component, transform=None, collection=None):
        self.name = name
        self.component = component
        self.objectType = 'adsk::fusion::Occurrence'
        self.assemblyContext = None
        self.nativeObject = None
        self.attributes = core.Attributes(self)
        self.transform2 = transform.copy() if transform is not None else core.Matrix3D.create()
        self.timelineObject = None
        self.isLightBulbOn = True
        self.isVisible = True
        self._collection = collection
        self._deleted = False
        _token(self, prefix='occurrence')

    @property
    def bRepBodies(self):
        """The component's bodies as seen through this occurrence (proxies)."""
        return BRepBodies(self.component, [b.createForAssemblyContext(self) for b in self.component.bRepBodies])

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    def _apply(self, cells):
        """Move: the occurrence transform becomes cells · transform2."""
        moved = core.Matrix3D.create()
        moved.setWithArray(core._multiply(cells, self.transform2._cells))
        self.transform2 = moved

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        # Seen in Fusion 2026-11-08: an occurrence whose row is after the marker does not exist
        # in the current state and cannot be deleted (a rolled-back feature can be).
        tl = self.timelineObject
        if tl is not None:
            group = tl.parentGroup
            position = group.index if group is not None and group.isCollapsed else tl.index
            if tl._timeline.markerPosition <= position:
                raise RuntimeError('2 : InternalValidationError : Xl::Utils::findObjectPath(this, objPath)')
        self._deleted = True
        if self._collection is not None and self in self._collection._items:
            self._collection._items.remove(self)
        if self.timelineObject is not None:
            self.timelineObject._timeline._remove(self.timelineObject)
        return True


class Occurrences(core.Base):
    """The occurrences directly under one component."""

    def __init__(self, component):
        self._component = component
        self._items = []

    def addExistingComponent(self, component, transform):
        """A new occurrence of ``component`` placed by ``transform``, named ``Name:k``; it
        gets a timeline row like a feature."""
        component._occurrence_count += 1
        occurrence = Occurrence(f'{component.name}:{component._occurrence_count}', component, transform, self)
        occurrence.timelineObject = self._component._timeline._insert(occurrence)
        self._items.append(occurrence)
        return occurrence

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def itemByName(self, name):
        return next((o for o in self._items if o.name == name), None)

    def asArray(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class Component(core.Base):
    def __init__(self, name, timeline, design=None):
        self.name = name
        self.objectType = 'adsk::fusion::Component'
        self._timeline = timeline
        self._design = design
        self.features = Features(timeline, self)
        self.bRepBodies = BRepBodies(self)
        self.occurrences = Occurrences(self)
        self._occurrence_count = 0
        self._body_count = 0
        _token(self, 'component/' + name)

    def body(self, name=None, origin=(0.0, 0.0, 0.0)):
        """Test helper: a body in this component with its origin at ``origin`` (cm)."""
        return self._new_body(name, origin)

    def _new_body(self, name=None, origin=(0.0, 0.0, 0.0)):
        self._body_count += 1
        body = BRepBody(name or f'Body{self._body_count}', self, origin)
        self.bRepBodies._items.append(body)
        return body

    @property
    def allOccurrences(self):
        """Every occurrence under this component, at any depth."""
        found = core.ObjectCollection.create()

        def walk(component):
            for occurrence in component.occurrences:
                found.add(occurrence)
                walk(occurrence.component)
        walk(self)
        return found

    def allOccurrencesByComponent(self, component):
        found = core.ObjectCollection.create()
        for occurrence in self.allOccurrences:
            if occurrence.component is component:
                found.add(occurrence)
        return found


class Design(core.Base):
    def __init__(self, length_unit='mm'):
        self.timeline = Timeline()
        self.rootComponent = Component('Root', self.timeline, self)
        self.activeComponent = self.rootComponent
        self.userParameters = UserParameters()
        self.unitsManager = core.UnitsManager(length_unit)
        self.fusionUnitsManager = self.unitsManager

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, Design) else None

    def findAttributes(self, groupName, attributeName):
        return core.Attributes.find(groupName, attributeName)

    def findEntityByToken(self, entityToken):
        """The live objects carrying this token (a list, as in Fusion)."""
        return [o for o in _tokens.get(entityToken, []) if not getattr(o, '_deleted', False)]

    def add_component(self, name, occurrence_name):
        """Test helper: a child component with one occurrence under the root (identity
        transform), named ``occurrence_name``."""
        component = Component(name, self.timeline, self)
        occurrence = self.rootComponent.occurrences.addExistingComponent(component, core.Matrix3D.create())
        occurrence.name = occurrence_name
        return component


class Entity(core.Base):
    """Any selectable thing: a body, a feature, an edge … (``component`` None: in the root).
    A face, edge or vertex is made with ``body=``: as in Fusion it knows its body, and only
    the body knows its component. ``start`` / ``end`` (cm) give a linear edge, sketch line or
    construction axis its geometry; a vertex or construction point sits at ``start``."""

    def __init__(self, name, object_type='BRepEdge', component=None, body=None, start=None, end=None):
        self.name = name
        self.objectType = 'adsk::fusion::' + object_type
        if body is not None:
            self.body = body
        else:
            self.parentComponent = component
        self.assemblyContext = None
        self.nativeObject = None
        self.attributes = core.Attributes(self)
        self._start = tuple(float(v) for v in (start if start is not None else (0.0, 0.0, 0.0)))
        self._end = tuple(float(v) for v in (end if end is not None else (1.0, 0.0, 0.0)))
        _token(self, prefix='entity')

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    @property
    def geometry(self):
        kind = self.objectType.rsplit('::', 1)[-1]
        start = core.Point3D.create(*self._start)
        if kind in ('BRepEdge', 'SketchLine'):
            return core.Line3D.create(start, core.Point3D.create(*self._end))
        if kind == 'ConstructionAxis':
            direction = start.vectorTo(core.Point3D.create(*self._end))
            direction.normalize()
            return core.InfiniteLine3D.create(start, direction)
        if kind in ('BRepVertex', 'ConstructionPoint', 'SketchPoint'):
            return start
        return None

    @property
    def worldGeometry(self):
        return self.geometry


# ---- sketches and sketch pattern constraints ----

def _map(cells, xy):
    x, y, _ = core._apply_point(cells, xy[0], xy[1], 0.0)
    return (x, y)


def _scaled(xy, centre, factor):
    return tuple(c + factor * (v - c) for v, c in zip(xy, centre))


class SketchEntity(core.Base):
    """Geometry lives in ``_geom`` (shared with proxies, which copy ``__dict__``), in sketch
    coordinates. The fake's sketch plane is the model XY plane, so worldGeometry is
    geometry."""

    def __init__(self, sketch, name):
        self.parentSketch = sketch
        self.name = name
        _token(self, f'{sketch.name}/{name}')
        self.objectType = 'adsk::fusion::' + type(self).__name__
        self.nativeObject = None      # set on a proxy (entity seen through an occurrence)
        self.assemblyContext = None
        self.isConstruction = False
        self.attributes = core.Attributes(self)
        self.geometricConstraints = []   # shared with proxies (see proxy())
        self._geom = {}
        self._deleted = False

    def proxy(self, occurrence_name):
        """The same entity as Fusion hands it out when selected inside an occurrence."""
        return _proxy(self, Entity(occurrence_name, 'Occurrence'))

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._deleted = True
        self.parentSketch.deleted.append(self)
        return True

    @property
    def geometry(self):
        return None

    @property
    def worldGeometry(self):
        return self.geometry

    def _corners(self):
        """Sketch-space points that bound the entity."""
        return [(0.0, 0.0)]

    @property
    def boundingBox(self):
        corners = self._corners()
        box = core.BoundingBox3D.create(core.Point3D.create(*corners[0]), core.Point3D.create(*corners[0]))
        for x, y in corners[1:]:
            box.expand(core.Point3D.create(x, y, 0.0))
        return box

    def _clone(self, sketch, name):
        raise NotImplementedError

    def _transform(self, cells):
        raise NotImplementedError

    def _scale_about(self, centre, factor):
        raise NotImplementedError


class SketchPoint(SketchEntity):
    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, SketchPoint) else None

    def __init__(self, sketch, name, at=(0.0, 0.0)):
        super().__init__(sketch, name)
        self._geom['at'] = (float(at[0]), float(at[1]))

    @property
    def geometry(self):
        return core.Point3D.create(*self._geom['at'])

    def _corners(self):
        return [self._geom['at']]

    def _clone(self, sketch, name):
        return SketchPoint(sketch, name, self._geom['at'])

    def _transform(self, cells):
        self._geom['at'] = _map(cells, self._geom['at'])

    def _scale_about(self, centre, factor):
        self._geom['at'] = _scaled(self._geom['at'], centre, factor)


class SketchLine(SketchEntity):
    def __init__(self, sketch, name, start=(0.0, 0.0), end=(1.0, 0.0)):
        super().__init__(sketch, name)
        self._geom['start'] = (float(start[0]), float(start[1]))
        self._geom['end'] = (float(end[0]), float(end[1]))

    @property
    def geometry(self):
        return core.Line3D.create(core.Point3D.create(*self._geom['start']),
                                  core.Point3D.create(*self._geom['end']))

    @property
    def length(self):
        return math.dist(self._geom['start'], self._geom['end'])

    def _corners(self):
        return [self._geom['start'], self._geom['end']]

    def _clone(self, sketch, name):
        return SketchLine(sketch, name, self._geom['start'], self._geom['end'])

    def _transform(self, cells):
        self._geom['start'] = _map(cells, self._geom['start'])
        self._geom['end'] = _map(cells, self._geom['end'])

    def _scale_about(self, centre, factor):
        self._geom['start'] = _scaled(self._geom['start'], centre, factor)
        self._geom['end'] = _scaled(self._geom['end'], centre, factor)


class SketchCircle(SketchEntity):
    def __init__(self, sketch, name, centre=(0.0, 0.0), radius=1.0):
        super().__init__(sketch, name)
        self.centerSketchPoint = SketchPoint(sketch, name + ' centre', centre)
        self._geom['radius'] = float(radius)

    @property
    def _centre(self):
        return self.centerSketchPoint._geom['at']

    @property
    def radius(self):
        return self._geom['radius']

    @radius.setter
    def radius(self, value):
        if value <= 0:
            raise RuntimeError('3 : invalid argument value')
        self._geom['radius'] = float(value)

    @property
    def geometry(self):
        return core.Circle3D.createByCenter(core.Point3D.create(*self._centre),
                                            core.Vector3D.create(0.0, 0.0, 1.0), self.radius)

    def _corners(self):
        (x, y), r = self._centre, self.radius
        return [(x - r, y - r), (x + r, y + r)]

    def _clone(self, sketch, name):
        return SketchCircle(sketch, name, self._centre, self.radius)

    def _transform(self, cells):
        self.centerSketchPoint._transform(cells)
        # A uniform scale in the matrix scales the radius; a rigid transform leaves it alone.
        self._geom['radius'] *= math.sqrt(cells[0] ** 2 + cells[4] ** 2 + cells[8] ** 2)

    def _scale_about(self, centre, factor):
        self.centerSketchPoint._scale_about(centre, factor)
        self._geom['radius'] *= factor


class RectangularPatternConstraintInput(core.Base):
    def __init__(self, entities, distance_type):
        self.entities = list(entities)
        self.distanceType = distance_type
        self.directionOneEntity = self.directionTwoEntity = None
        self.quantityOne = self.quantityTwo = None
        self.distanceOne = self.distanceTwo = None
        self.isSymmetricInDirectionOne = self.isSymmetricInDirectionTwo = False

    @staticmethod
    def _check_line(entity, argument):
        # Seen in Fusion (2026-11): the set-methods reject None, although the docs say null
        # means the default axis, and they reject occurrence proxies. The properties start
        # out null and only need setting when there is a real line.
        if entity is None:
            raise RuntimeError(f'3 : invalid argument {argument}')
        if getattr(entity, 'nativeObject', None) is not None:
            raise RuntimeError(f'3 : invalid argument {argument}')

    def setDirectionOne(self, directionOneEntity, quantityOne, distanceOne):
        self._check_line(directionOneEntity, 'directionOneEntity')
        self.directionOneEntity, self.quantityOne, self.distanceOne = directionOneEntity, quantityOne, distanceOne
        return True

    def setDirectionTwo(self, directionTwoEntity, quantityTwo, distanceTwo):
        self._check_line(directionTwoEntity, 'directionTwoEntity')
        self.directionTwoEntity, self.quantityTwo, self.distanceTwo = directionTwoEntity, quantityTwo, distanceTwo
        return True


class CircularPatternConstraintInput(core.Base):
    def __init__(self, entities, center):
        self.entities = list(entities)
        self.centerPoint = center
        self.quantity = None
        self.totalAngle = None
        self.isSymmetric = False


class PatternConstraint(core.Base):
    def __init__(self, constraints, pattern_input, copies):
        self._constraints = constraints
        self.input = pattern_input
        self.attributes = core.Attributes(self)
        self.parentSketch = constraints._sketch
        self.entities = list(pattern_input.entities)
        self.createdEntities = copies
        kind = 'Circular' if isinstance(pattern_input, CircularPatternConstraintInput) else 'Rectangular'
        self.objectType = f'adsk::fusion::{kind}PatternConstraint'
        self._deleted = False
        # Fusion lists the pattern constraint in a copied curve's geometricConstraints (seen
        # 2026-11-06). The fake leaves that link out on purpose, so the add-in's other way of
        # recognising a clicked curve, matching entity tokens against the known patterns,
        # stays covered by the tests.

    @property
    def directionOneEntity(self):
        return getattr(self.input, 'directionOneEntity', None)

    @property
    def centerPoint(self):
        return getattr(self.input, 'centerPoint', None)

    def deleteMe(self):
        """Removes the constraint; the copied geometry stays, as in Fusion."""
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._deleted = True
        self._constraints.created.remove(self)
        return True


class GeometricConstraints(core.Base):
    def __init__(self, sketch):
        self._sketch = sketch
        self.created = []
        self.fail_at = None   # test hook: make add…Pattern() return None for this copy number

    def _check(self, entities):
        for e in entities:
            if e.parentSketch is not self._sketch:
                raise RuntimeError('3 : InternalValidationError : entity not in this sketch')
            if e.nativeObject is not None:
                raise RuntimeError('3 : invalid argument entities')

    def createRectangularPatternInput(self, entities, distanceType):
        self._check(entities)
        return RectangularPatternConstraintInput(entities, distanceType)

    def addRectangularPattern(self, pattern_input):
        return self._add(pattern_input)

    def createCircularPatternInput(self, inputEntities, centerPoint):
        self._check(inputEntities)
        if centerPoint.parentSketch is not self._sketch:
            raise RuntimeError('3 : InternalValidationError : centre point not in this sketch')
        return CircularPatternConstraintInput(inputEntities, centerPoint)

    def addCircularPattern(self, pattern_input):
        return self._add(pattern_input)

    def _add(self, pattern_input):
        if self.fail_at is not None and len(self.created) + 1 == self.fail_at:
            return None
        k = self._sketch._next_copy()
        # The copies sit on the originals: the fake does not compute pattern positions.
        copies = [e._clone(self._sketch, f'{e.name} copy {k}') for e in pattern_input.entities]
        constraint = PatternConstraint(self, pattern_input, copies)
        self.created.append(constraint)
        return constraint

    @property
    def count(self):
        return len(self.created)

    def item(self, index):
        return self.created[index]


class _Plane(core.Base):
    """core.Plane as a profile hands it out: an origin and a normal."""

    def __init__(self, origin, normal):
        self.origin = origin
        self.normal = normal


class AreaProperties(core.Base):
    def __init__(self, centroid, area=1.0):
        self.centroid = centroid
        self.area = area
        self.perimeter = 4.0 * math.sqrt(area)


class Profile(core.Base):
    """A closed sketch region. The fake knows its centroid only (a Profile has no
    attributes in Fusion, so none here)."""

    def __init__(self, sketch, name, centroid=(0.0, 0.0, 0.0)):
        self.parentSketch = sketch
        self.name = name              # test helper; a Fusion profile has no name
        self.objectType = 'adsk::fusion::Profile'
        self.nativeObject = None
        self.assemblyContext = None
        self.isOnSketchPlane = True
        self._centroid = tuple(float(v) for v in centroid)
        _token(self, f'{sketch.name}/{name}')

    def areaProperties(self, accuracy=0):
        return AreaProperties(core.Point3D.create(*self._centroid))

    @property
    def plane(self):
        """The profile's plane: through the centroid, normal +Z (the fake's sketches lie on XY)."""
        return _Plane(core.Point3D.create(*self._centroid), core.Vector3D.create(0.0, 0.0, 1.0))

    @property
    def boundingBox(self):
        point = core.Point3D.create(*self._centroid)
        return core.BoundingBox3D.create(point, point)

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)


class Profiles(core.Base):
    def __init__(self, sketch):
        self._sketch = sketch
        self._items = []

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class Sketch(core.Base):
    def __init__(self, name='Sketch1', component=None):
        self.name = name
        self.objectType = 'adsk::fusion::Sketch'
        _token(self, 'sketch/' + name)
        self.parentComponent = component      # None: in the root component
        self.geometricConstraints = GeometricConstraints(self)
        self.profiles = Profiles(self)
        self.transform = core.Matrix3D.create()   # sketch space is model space in the fake
        self.deleted = []           # test helper: entities deleted from this sketch
        self.copies = []            # test helper: entities made by copy(), in order
        self._made = 0              # copy numbers, shared by copy() and the pattern constraints

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, Sketch) else None

    @property
    def origin(self):
        return self.transform.getAsCoordinateSystem()[0]

    @property
    def xDirection(self):
        return self.transform.getAsCoordinateSystem()[1]

    @property
    def yDirection(self):
        return self.transform.getAsCoordinateSystem()[2]

    def sketchToModelSpace(self, sketchCoordinate):
        """Sketch → model coordinates through the sketch's transform (identity in the fake)."""
        point = sketchCoordinate.copy()
        inverse = self.transform.copy()
        inverse.invert()
        point.transformBy(inverse)
        return point

    def modelToSketchSpace(self, modelCoordinate):
        point = modelCoordinate.copy()
        point.transformBy(self.transform)
        return point

    def line(self, name, start=(0.0, 0.0), end=(1.0, 0.0)):
        return SketchLine(self, name, start, end)

    def point(self, name, at=(0.0, 0.0)):
        return SketchPoint(self, name, at)

    def circle(self, name, centre=(0.0, 0.0), radius=1.0):
        return SketchCircle(self, name, centre, radius)

    def profile(self, name, centroid=(0.0, 0.0, 0.0)):
        """Test helper: a profile of this sketch with the given centroid (cm)."""
        p = Profile(self, name, centroid)
        self.profiles._items.append(p)
        return p

    def _next_copy(self):
        self._made += 1
        return self._made

    def _check(self, entities, argument):
        for e in entities:
            if getattr(e, 'parentSketch', None) is not self:
                raise RuntimeError(f'3 : invalid argument {argument}')

    def copy(self, sketchEntities, transform, targetSketch=None):
        """New entities of the same kinds, named ``<name> copy k``, with the geometry put
        through ``transform``; returns them as an ObjectCollection."""
        entities = list(sketchEntities)
        self._check(entities, 'sketchEntities')
        target = targetSketch if targetSketch is not None else self
        k = target._next_copy()
        made = core.ObjectCollection.create()
        for e in entities:
            c = e._clone(target, f'{e.name} copy {k}')
            c.isConstruction = e.isConstruction
            c._transform(transform._cells)
            target.copies.append(c)
            made.add(c)
        return made

    def scale(self, sketchEntities, referencePoint, scaleFactor):
        """Scales the entities' geometry about ``referencePoint`` (a Point3D in sketch
        space); a circle's radius scales too."""
        entities = list(sketchEntities)
        self._check(entities, 'sketchEntities')
        centre = (referencePoint.x, referencePoint.y)
        for e in entities:
            e._scale_about(centre, float(scaleFactor))
        return True
