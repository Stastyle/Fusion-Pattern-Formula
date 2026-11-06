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


class TimelineGroup(core.Base):
    def __init__(self, start, end):
        self.start, self.end = start, end
        self.name = ''
        self.isCollapsed = True


class TimelineGroups(core.Base):
    def __init__(self):
        self._groups = []

    def add(self, startIndex, endIndex):
        if endIndex <= startIndex:
            raise ValueError('a timeline group needs at least two items')
        g = TimelineGroup(startIndex, endIndex)
        self._groups.append(g)
        return g

    @property
    def count(self):
        return len(self._groups)

    def item(self, index):
        return self._groups[index]


class Timeline(core.Base):
    def __init__(self):
        self.markerPosition = 0
        self.timelineGroups = TimelineGroups()


class Feature(core.Base):
    def __init__(self, pattern_input):
        self.input = pattern_input
        self.name = ''
        self.attributes = core.Attributes()
        self.isSuppressed = False


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
    def __init__(self, timeline):
        self._timeline = timeline
        self.created = []
        self.fail_at = None   # test hook: make add() return None for this copy number
        self.reject = None    # test hook: createInput raises "3 : invalid argument <reject>"

    def _reject(self):
        if self.reject:
            raise RuntimeError(f'3 : invalid argument {self.reject}')

    def add(self, pattern_input):
        if self.fail_at is not None and len(self.created) + 1 == self.fail_at:
            return None
        feature = Feature(pattern_input)
        self.created.append(feature)
        self._timeline.markerPosition += 1
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


class Features(core.Base):
    def __init__(self, timeline):
        self.rectangularPatternFeatures = RectangularPatternFeatures(timeline)
        self.circularPatternFeatures = CircularPatternFeatures(timeline)


class Component(core.Base):
    def __init__(self, name, timeline):
        self.name = name
        self.features = Features(timeline)


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


class Entity(core.Base):
    """Any selectable thing: a body, a feature, an edge …"""

    def __init__(self, name, object_type='BRepEdge'):
        self.name = name
        self.objectType = 'adsk::fusion::' + object_type
        self.assemblyContext = None


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

    def proxy(self, occurrence_name):
        """The same entity as Fusion hands it out when selected inside an occurrence."""
        p = type(self).__new__(type(self))
        p.__dict__.update(self.__dict__)
        p.nativeObject = self
        p.assemblyContext = Entity(occurrence_name)
        return p


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
    def __init__(self, pattern_input):
        self.input = pattern_input
        self.attributes = core.Attributes()


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
        constraint = PatternConstraint(pattern_input)
        self.created.append(constraint)
        return constraint


class Sketch(core.Base):
    def __init__(self, name='Sketch1'):
        self.name = name
        self.entityToken = 'sketch/' + name
        self.geometricConstraints = GeometricConstraints(self)

    @classmethod
    def cast(cls, obj):
        return obj if isinstance(obj, Sketch) else None

    def line(self, name):
        return SketchLine(self, name)

    def point(self, name):
        return SketchPoint(self, name)

    def circle(self, name):
        return SketchCircle(self, name)
