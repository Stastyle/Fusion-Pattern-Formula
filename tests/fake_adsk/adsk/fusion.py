"""Fake adsk.fusion — see package docstring."""
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
        """A feature created while rolled back goes in at the marker, which then moves past it."""
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
    _tokens = 0

    def __init__(self, collection, pattern_input, name=''):
        self._collection = collection
        self.input = pattern_input
        self.name = name
        self.attributes = core.Attributes(self)
        self.isSuppressed = False
        self.timelineObject = None
        self.objectType = 'adsk::fusion::' + type(collection).__name__[:-1]   # RectangularPatternFeature …
        Feature._tokens += 1
        self.entityToken = f'feature/{Feature._tokens}'
        self.parentComponent = collection._component
        self.assemblyContext = None
        self.nativeObject = None
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

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._deleted = True
        self._collection.created.remove(self)
        self._collection._timeline._remove(self.timelineObject)
        return True


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


class _PatternFeatures(core.Base):
    def __init__(self, timeline, component=None):
        self._timeline = timeline
        self._component = component
        self.created = []
        self.fail_at = None   # test hook: make add() return None for this copy number
        self.reject = None    # test hook: createInput raises "3 : invalid argument <reject>"

    def _reject(self):
        if self.reject:
            raise RuntimeError(f'3 : invalid argument {self.reject}')

    def add(self, pattern_input):
        if self.fail_at is not None and len(self.created) + 1 == self.fail_at:
            return None
        feature = Feature(self, pattern_input)
        feature.timelineObject = self._timeline._insert(feature)
        self.created.append(feature)
        return feature

    @property
    def count(self):
        return len(self.created)

    def item(self, index):
        return self.created[index]


class RectangularPatternFeatures(_PatternFeatures):
    def createInput(self, inputEntities, directionOneEntity, quantityOne, distanceOne, patternDistanceType):
        self._reject()
        return RectangularPatternFeatureInput(inputEntities, directionOneEntity, quantityOne, distanceOne,
                                             patternDistanceType)


class CircularPatternFeatures(_PatternFeatures):
    def createInput(self, inputEntities, axis):
        self._reject()
        return CircularPatternFeatureInput(inputEntities, axis)


class OtherFeatures(_PatternFeatures):
    """Test helper: any other feature (a fillet, say) to put before or after a pattern."""

    def add(self, name):
        feature = Feature(self, None, name)
        feature.objectType = 'adsk::fusion::FilletFeature'
        feature.timelineObject = self._timeline._insert(feature)
        self.created.append(feature)
        return feature


class Features(core.Base):
    def __init__(self, timeline, component=None):
        self.rectangularPatternFeatures = RectangularPatternFeatures(timeline, component)
        self.circularPatternFeatures = CircularPatternFeatures(timeline, component)
        self.otherFeatures = OtherFeatures(timeline, component)


class Occurrence(core.Base):
    def __init__(self, name, component):
        self.name = name
        self.component = component
        self.objectType = 'adsk::fusion::Occurrence'
        self.assemblyContext = None
        self.attributes = core.Attributes(self)


class Component(core.Base):
    def __init__(self, name, timeline):
        self.name = name
        self.entityToken = 'component/' + name
        self.features = Features(timeline, self)
        self._occurrences = []        # on the root: every occurrence in the design

    def allOccurrencesByComponent(self, component):
        found = core.ObjectCollection.create()
        for occ in self._occurrences:
            if occ.component is component:
                found.add(occ)
        return found


class Design(core.Base):
    def __init__(self, length_unit='mm'):
        self.timeline = Timeline()
        self.rootComponent = Component('Root', self.timeline)
        self.activeComponent = self.rootComponent
        self.userParameters = UserParameters()
        self.unitsManager = core.UnitsManager(length_unit)
        self.fusionUnitsManager = self.unitsManager

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, Design) else None

    def findAttributes(self, groupName, attributeName):
        return core.Attributes.find(groupName, attributeName)

    def add_component(self, name, occurrence_name):
        """Test helper: a child component with one occurrence under the root."""
        component = Component(name, self.timeline)
        self.rootComponent._occurrences.append(Occurrence(occurrence_name, component))
        return component


class Entity(core.Base):
    """Any selectable thing: a body, a feature, an edge … (``component`` None: in the root).
    A face, edge or vertex is made with ``body=``: as in Fusion it knows its body, and only
    the body knows its component."""

    def __init__(self, name, object_type='BRepEdge', component=None, body=None):
        self.name = name
        self.objectType = 'adsk::fusion::' + object_type
        if body is not None:
            self.body = body
        else:
            self.parentComponent = component
        self.assemblyContext = None
        self.nativeObject = None
        self.attributes = core.Attributes(self)

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)


# ---- sketches and sketch pattern constraints ----

class SketchEntity(core.Base):
    def __init__(self, sketch, name):
        self.parentSketch = sketch
        self.name = name
        self.entityToken = f'{sketch.name}/{name}'
        self.objectType = 'adsk::fusion::' + type(self).__name__
        self.nativeObject = None      # set on a proxy (entity seen through an occurrence)
        self.assemblyContext = None
        self.isConstruction = False
        self.attributes = core.Attributes(self)
        self.geometricConstraints = []   # shared with proxies (see proxy())
        self._deleted = False

    def proxy(self, occurrence_name):
        """The same entity as Fusion hands it out when selected inside an occurrence."""
        return _proxy(self, Entity(occurrence_name))

    def createForAssemblyContext(self, occurrence):
        return _proxy(self, occurrence)

    def deleteMe(self):
        if self._deleted:
            raise RuntimeError('3 : InternalValidationError : deleted')
        self._deleted = True
        self.parentSketch.deleted.append(self)
        return True


class SketchPoint(SketchEntity):
    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, SketchPoint) else None


class SketchLine(SketchEntity):
    pass


class SketchCircle(SketchEntity):
    def __init__(self, sketch, name):
        super().__init__(sketch, name)
        self.centerSketchPoint = SketchPoint(sketch, name + ' centre')


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
        for e in self.entities + copies:
            e.geometricConstraints.append(self)

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
        for e in self.entities + self.createdEntities:
            if self in e.geometricConstraints:
                e.geometricConstraints.remove(self)
        return True


class GeometricConstraints(core.Base):
    def __init__(self, sketch):
        self._sketch = sketch
        self.created = []
        self.fail_at = None   # test hook: make add…Pattern() return None for this copy number
        self._made = 0

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
        self._made += 1
        copies = [type(e)(self._sketch, f'{e.name} copy {self._made}') for e in pattern_input.entities]
        constraint = PatternConstraint(self, pattern_input, copies)
        self.created.append(constraint)
        return constraint

    @property
    def count(self):
        return len(self.created)

    def item(self, index):
        return self.created[index]


class Sketch(core.Base):
    def __init__(self, name='Sketch1', component=None):
        self.name = name
        self.entityToken = 'sketch/' + name
        self.parentComponent = component      # None: in the root component
        self.geometricConstraints = GeometricConstraints(self)
        self.deleted = []           # test helper: entities deleted from this sketch

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, Sketch) else None

    def line(self, name):
        return SketchLine(self, name)

    def point(self, name):
        return SketchPoint(self, name)

    def circle(self, name):
        return SketchCircle(self, name)
