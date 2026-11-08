"""Fake adsk.core — see package docstring."""
import math
import re


class Base:
    @classmethod
    def cast(cls, obj):
        return obj


# ---- values and enums ----

class ValueInput(Base):
    def __init__(self, real=None, string=None):
        self.realValue = real
        self.stringValue = string

    @staticmethod
    def createByReal(value):
        return ValueInput(real=float(value))

    @staticmethod
    def createByString(value):
        return ValueInput(string=str(value))


class DropDownStyles:
    LabeledIconDropDownStyle = 0
    TextListDropDownStyle = 1
    CheckBoxDropDownStyle = 2


class CommandTerminationReason:
    UnknownTerminationReason = 0
    CompletedTerminationReason = 1
    CancelledTerminationReason = 2
    AbortedTerminationReason = 3


# ---- events ----

class Event:
    def __init__(self):
        self.handlers = []

    def add(self, handler):
        self.handlers.append(handler)
        return True

    def remove(self, handler):
        self.handlers.remove(handler)
        return True

    def fire(self, args):
        for handler in list(self.handlers):
            handler.notify(args)
        return args


class CommandCreatedEventHandler:
    pass


class InputChangedEventHandler:
    pass


class ValidateInputsEventHandler:
    pass


class CommandEventHandler:
    pass


class CommandCreatedEventArgs:
    def __init__(self, command):
        self.command = command


class InputChangedEventArgs:
    def __init__(self, input, inputs):
        self.input = input
        self.inputs = inputs


class ValidateInputsEventArgs:
    def __init__(self):
        self.areInputsValid = True


class CommandEventArgs:
    def __init__(self, command, terminationReason=0):
        self.command = command
        self.isValidResult = False
        self.executeFailed = False
        self.executeFailedMessage = ''
        self.terminationReason = terminationReason


# ---- command inputs ----

class ListItem(Base):
    def __init__(self, parent, name, index):
        self._parent = parent
        self.name = name
        self.index = index

    @property
    def isSelected(self):
        return self._parent._selected is self

    @isSelected.setter
    def isSelected(self, value):
        if value:
            self._parent._selected = self
        elif self._parent._selected is self:
            self._parent._selected = None


class ListItems(Base):
    def __init__(self):
        self._items = []
        self._selected = None

    def add(self, name, isSelected, icon='', beforeIndex=-1):
        item = ListItem(self, name, len(self._items))
        self._items.append(item)
        if isSelected:
            self._selected = item
        return item

    def clear(self):
        self._items = []
        self._selected = None
        return True

    def item(self, index):
        return self._items[index]

    @property
    def count(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class CommandInput(Base):
    def __init__(self, inputs, id, name):
        self._inputs = inputs
        self.id = id
        self.name = name
        self.isVisible = True
        self.isEnabled = True
        self.isFullWidth = False
        self.tooltip = ''
        self.tooltipDescription = ''

    @property
    def parentCommand(self):
        return self._inputs.command


class DropDownCommandInput(CommandInput):
    def __init__(self, inputs, id, name, style):
        super().__init__(inputs, id, name)
        self.dropDownStyle = style
        self.listItems = ListItems()

    @property
    def selectedItem(self):
        return self.listItems._selected


class Selection(Base):
    def __init__(self, entity):
        self.entity = entity
        self.point = None


class SelectionCommandInput(CommandInput):
    def __init__(self, inputs, id, name, prompt):
        super().__init__(inputs, id, name)
        self.commandPrompt = prompt
        self.selectionFilters = []
        self._min, self._max = 1, 1
        self._selections = []
        self._has_focus = False
        self.fail_next_add = 0     # test hook: refuse the next n addSelection() calls

    @property
    def hasFocus(self):
        return self._has_focus

    @hasFocus.setter
    def hasFocus(self, value):
        # Documented: only one selection input has focus, and an invisible one cannot take it.
        if value and not self.isVisible:
            raise RuntimeError('3 : InternalValidationError : hasFocus')
        if value:
            for other in self._inputs._registry.values():
                if isinstance(other, SelectionCommandInput):
                    other._has_focus = False
        self._has_focus = bool(value)

    def addSelectionFilter(self, f):
        self.selectionFilters.append(f)
        return True

    def clearSelectionFilter(self):
        self.selectionFilters = []
        return True

    def setSelectionLimits(self, minimum, maximum=0):
        self._min, self._max = minimum, maximum
        return True

    def getSelectionLimits(self):
        return True, self._min, self._max

    @property
    def selectionCount(self):
        return len(self._selections)

    def selection(self, index):
        return self._selections[index]

    def addSelection(self, entity):
        if self.fail_next_add:
            self.fail_next_add -= 1
            return False
        if not self._has_focus:
            # Seen in Fusion (2026-11): programmatic selections into an input without focus
            # never showed up, and OK stayed disabled. Give the input focus first.
            return False
        self._selections.append(Selection(entity))
        return True

    def clearSelection(self):
        self._selections = []
        return True


class BoolValueCommandInput(CommandInput):
    def __init__(self, inputs, id, name, isCheckBox, resourceFolder, initialValue):
        super().__init__(inputs, id, name)
        self.isCheckBox = isCheckBox
        self.resourceFolder = resourceFolder
        self.value = initialValue


class IntegerSpinnerCommandInput(CommandInput):
    def __init__(self, inputs, id, name, minimum, maximum, step, initial):
        super().__init__(inputs, id, name)
        self.minimumValue, self.maximumValue, self.spinStep = minimum, maximum, step
        self._value = initial

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        self._value = max(self.minimumValue, min(self.maximumValue, int(v)))


class StringValueCommandInput(CommandInput):
    def __init__(self, inputs, id, name, initial):
        super().__init__(inputs, id, name)
        self.value = initial
        self.isValueError = False
        self.isReadOnly = False
        self.isPassword = False


_LENGTH_TO_CM = {'cm': 1.0, 'mm': 0.1, 'm': 100.0, 'in': 2.54, 'ft': 30.48}
_ANGLE_TO_RAD = {'rad': 1.0, 'deg': math.pi / 180.0}


NOT_READY = '2 : InternalValidationError : res'


class ValueCommandInput(CommandInput):
    """Keeps ``value`` in internal units (cm / rad) and ``expression`` as display text.

    Mirrors a Fusion quirk seen in the field: a *unitless* value input (unit type '') is
    not evaluated until the dialog is on screen. Before the command is activated its
    isValidExpression is False and reading or writing value / expression raises
    ``RuntimeError: 2 : InternalValidationError : res``. Inputs with a unit type work
    straight away.
    """

    def __init__(self, inputs, id, name, unitType, initial):
        super().__init__(inputs, id, name)
        self.unitType = unitType
        self._valid = True
        self._value = 0.0
        self._expression = ''
        self._assign_value(initial.realValue if initial.realValue is not None else 0.0)
        if initial.stringValue is not None:
            self._assign_expression(initial.stringValue)

    def _ready(self):
        return self.unitType != '' or self._inputs.command.isActivated

    def _require_ready(self):
        if not self._ready():
            raise RuntimeError(NOT_READY)

    @property
    def isValidExpression(self):
        return self._ready() and self._valid

    @property
    def value(self):
        self._require_ready()
        return self._value

    @value.setter
    def value(self, v):
        self._require_ready()
        self._assign_value(v)

    def _assign_value(self, v):
        self._value = float(v)
        if self.unitType in _LENGTH_TO_CM:
            self._expression = f'{self._value / _LENGTH_TO_CM[self.unitType]:g} {self.unitType}'
        elif self.unitType in _ANGLE_TO_RAD:
            self._expression = f'{self._value / _ANGLE_TO_RAD[self.unitType]:g} {self.unitType}'
        else:
            self._expression = f'{self._value:g}'
        self._valid = True

    @property
    def expression(self):
        self._require_ready()
        return self._expression

    @expression.setter
    def expression(self, text):
        self._require_ready()
        self._assign_expression(text)

    def _assign_expression(self, text):
        self._expression = text
        m = re.fullmatch(r'\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*', text or '')
        if not m:
            self._valid = False
            return
        number, unit = float(m.group(1)), m.group(2) or self.unitType
        if self.unitType in _LENGTH_TO_CM and unit in _LENGTH_TO_CM:
            self._value = number * _LENGTH_TO_CM[unit]
        elif self.unitType in _ANGLE_TO_RAD and unit in _ANGLE_TO_RAD:
            self._value = number * _ANGLE_TO_RAD[unit]
        elif self.unitType == '' and unit == '':
            self._value = number
        else:
            self._valid = False
            return
        self._valid = True


class TextBoxCommandInput(CommandInput):
    def __init__(self, inputs, id, name, formattedText, numRows, isReadOnly):
        super().__init__(inputs, id, name)
        self.formattedText = formattedText
        self.numRows = numRows
        self.isReadOnly = isReadOnly

    @property
    def text(self):
        return re.sub(r'<[^>]+>', '', self.formattedText)


class GroupCommandInput(CommandInput):
    def __init__(self, inputs, id, name):
        super().__init__(inputs, id, name)
        self.isExpanded = True
        self.isEnabledCheckBoxDisplayed = False
        self.children = CommandInputs(inputs.command, inputs._registry)


class CommandInputs(Base):
    def __init__(self, command, registry=None):
        self.command = command
        self._registry = registry if registry is not None else {}
        self._items = []

    def _add(self, inp):
        if inp.id in self._registry:
            raise ValueError(f'duplicate command input id {inp.id!r}')
        self._registry[inp.id] = inp
        self._items.append(inp)
        return inp

    def itemById(self, id):
        return self._registry.get(id)

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def __iter__(self):
        return iter(self._items)

    def addDropDownCommandInput(self, id, name, style):
        return self._add(DropDownCommandInput(self, id, name, style))

    def addSelectionInput(self, id, name, prompt):
        return self._add(SelectionCommandInput(self, id, name, prompt))

    def addBoolValueInput(self, id, name, isCheckBox, resourceFolder='', initialValue=False):
        return self._add(BoolValueCommandInput(self, id, name, isCheckBox, resourceFolder, initialValue))

    def addIntegerSpinnerCommandInput(self, id, name, minimum, maximum, step, initial):
        return self._add(IntegerSpinnerCommandInput(self, id, name, minimum, maximum, step, initial))

    def addStringValueInput(self, id, name, initialValue=''):
        return self._add(StringValueCommandInput(self, id, name, initialValue))

    def addValueInput(self, id, name, unitType, initialValue):
        return self._add(ValueCommandInput(self, id, name, unitType, initialValue))

    def addTextBoxCommandInput(self, id, name, formattedText, numRows, isReadOnly):
        return self._add(TextBoxCommandInput(self, id, name, formattedText, numRows, isReadOnly))

    def addGroupCommandInput(self, id, name):
        return self._add(GroupCommandInput(self, id, name))


class Command(Base):
    def __init__(self):
        self.commandInputs = CommandInputs(self)
        self.inputChanged = Event()
        self.validateInputs = Event()
        self.executePreview = Event()
        self.execute = Event()
        self.destroy = Event()
        self.activate = Event()
        self.deactivate = Event()
        self.okButtonText = 'OK'
        self.cancelButtonText = 'Cancel'
        self.isRepeatable = True
        self.isOKButtonVisible = True
        self.sizes = []
        self.isActivated = False   # test hook: Fusion activates the command once the dialog is shown

    def show(self):
        """What Fusion does right after commandCreated: put the dialog on screen and fire activate."""
        self.isActivated = True
        return self.activate.fire(CommandEventArgs(self))

    def setDialogMinimumSize(self, w, h):
        self.sizes.append(('min', w, h))
        return True

    def setDialogInitialSize(self, w, h):
        self.sizes.append(('initial', w, h))
        return True

    def setDialogSize(self, w, h):
        self.sizes.append(('size', w, h))
        return True


# ---- command definitions and toolbars ----

class CommandDefinition(Base):
    def __init__(self, definitions, id, name, tooltip, resourceFolder):
        self._definitions = definitions
        self.id, self.name, self.tooltip, self.resourceFolder = id, name, tooltip, resourceFolder
        self.commandCreated = Event()
        self.toolClipFilename = ''

    def deleteMe(self):
        self._definitions._defs.pop(self.id, None)
        return True


class CommandDefinitions(Base):
    def __init__(self):
        self._defs = {}

    def itemById(self, id):
        return self._defs.get(id)

    def addButtonDefinition(self, id, name, tooltip, resourceFolder=''):
        d = CommandDefinition(self, id, name, tooltip, resourceFolder)
        self._defs[id] = d
        return d


class CommandControl(Base):
    def __init__(self, controls, definition, positionID, isBefore):
        self._controls = controls
        self.commandDefinition = definition
        self.id = definition.id
        self.positionID, self.isBefore = positionID, isBefore
        self.isPromoted = False
        self.isPromotedByDefault = False

    def deleteMe(self):
        self._controls._controls.remove(self)
        return True


class ToolbarControls(Base):
    def __init__(self):
        self._controls = []

    def itemById(self, id):
        return next((c for c in self._controls if c.id == id), None)

    def addCommand(self, definition, positionID='', isBefore=True):
        control = CommandControl(self, definition, positionID, isBefore)
        self._controls.append(control)
        return control

    @property
    def count(self):
        return len(self._controls)


class _NativeControl:
    def __init__(self, id):
        self.id = id


class ToolbarPanel(Base):
    def __init__(self, id):
        self.id = id
        self.name = id
        self.controls = ToolbarControls()


class _ById(Base):
    def __init__(self, items):
        self._items = {i.id: i for i in items}

    def itemById(self, id):
        return self._items.get(id)


class ToolbarTab(Base):
    def __init__(self, id, panels):
        self.id = id
        self.toolbarPanels = _ById(panels)


class Workspace(Base):
    def __init__(self, id, tabs):
        self.id = id
        self.toolbarTabs = _ById(tabs)


class UserInterface(Base):
    def __init__(self):
        self.commandDefinitions = CommandDefinitions()
        # Control ids as in Fusion's own toolbar definition (Resources/Toolbar/TabToolbars.xml).
        create_panel = ToolbarPanel('SolidCreatePanel')
        for native in ('Extrude', 'FusionHoleCommand', 'PatternRectangular', 'MirrorCommand'):
            create_panel.controls._controls.append(_NativeControl(native))
        self.create_panel = create_panel
        sketch_panel = ToolbarPanel('SketchCreatePanel')
        for native in ('DrawPolyline', 'MirrorSketchCommand', 'CircularSketchPatternCommand',
                       'RectangularSketchPatternCommand', 'SketchDimension'):
            sketch_panel.controls._controls.append(_NativeControl(native))
        self.sketch_panel = sketch_panel
        solid_tab = ToolbarTab('SolidTab', [create_panel])
        sketch_tab = ToolbarTab('SketchTab', [sketch_panel])
        self.workspaces = _ById([Workspace('FusionSolidEnvironment', [solid_tab, sketch_tab])])
        self.allToolbarPanels = _ById([create_panel, sketch_panel])
        self.messages = []

    def messageBox(self, text, title='', *args):
        self.messages.append(text)
        return 0


class UnitsManager(Base):
    def __init__(self, length_unit='mm'):
        self.defaultLengthUnits = length_unit
        self.internalUnits = 'cm'

    def convert(self, value, fromUnits, toUnits):
        if fromUnits in _LENGTH_TO_CM and toUnits in _LENGTH_TO_CM:
            return value * _LENGTH_TO_CM[fromUnits] / _LENGTH_TO_CM[toUnits]
        if fromUnits in _ANGLE_TO_RAD and toUnits in _ANGLE_TO_RAD:
            return value * _ANGLE_TO_RAD[fromUnits] / _ANGLE_TO_RAD[toUnits]
        raise ValueError(f'cannot convert {fromUnits} to {toUnits}')


class ObjectCollection(Base):
    def __init__(self):
        self._items = []

    @staticmethod
    def create():
        return ObjectCollection()

    def add(self, item):
        self._items.append(item)
        return True

    @property
    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]

    def asArray(self):
        return list(self._items)

    @staticmethod
    def createWithArray(array):
        c = ObjectCollection()
        c._items = list(array)
        return c

    def removeByItem(self, item):
        if item not in self._items:
            return False
        self._items.remove(item)
        return True

    def removeByIndex(self, index):
        del self._items[index]
        return True

    def contains(self, item):
        return item in self._items

    def clear(self):
        self._items = []
        return True

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def __getitem__(self, index):
        return self._items[index]


class Attribute(Base):
    def __init__(self, groupName, name, value, parent=None):
        self.groupName, self.name, self.value = groupName, name, value
        self.parent = parent


class Attributes(Base):
    """Attributes of one entity. Every attribute added anywhere is also recorded in a
    registry so Design.findAttributes() can find it, as in Fusion. Deleted entities carry
    ``_deleted = True`` and drop out of the search."""
    _registry = []

    def __init__(self, parent=None):
        self._parent = parent
        self._items = []

    def add(self, groupName, name, value):
        a = Attribute(groupName, name, value, self._parent)
        self._items.append(a)
        Attributes._registry.append(a)
        return a

    def itemByName(self, groupName, name):
        return next((a for a in self._items if a.groupName == groupName and a.name == name), None)

    @property
    def count(self):
        return len(self._items)

    @classmethod
    def find(cls, groupName, name):
        return [a for a in cls._registry
                if a.groupName == groupName and a.name == name and not getattr(a.parent, '_deleted', False)]


_reset_hooks = []   # callables run by Application.reset(); fusion.py adds its token registry


class Application(Base):
    _instance = None

    def __init__(self):
        self.userInterface = UserInterface()
        self.activeProduct = None
        self.activeEditObject = None   # a fusion.Sketch while the user is editing a sketch

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = Application()
        return cls._instance

    @classmethod
    def reset(cls):
        cls._instance = None
        Attributes._registry = []
        for hook in _reset_hooks:
            hook()


# ---- geometry ----
#
# Real maths, so the add-in's placement helpers can be checked against them. A matrix is
# 16 floats row-major, as Matrix3D.asArray() hands them out; the translation sits in the
# last column (cells 3, 7, 11). The private helpers work on plain cell lists and are shared
# with fusion.py.

def _identity():
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _multiply(a, b):
    """a · b: apply b first, then a."""
    return [sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def _apply_point(cells, x, y, z):
    return tuple(cells[r * 4] * x + cells[r * 4 + 1] * y + cells[r * 4 + 2] * z + cells[r * 4 + 3]
                 for r in range(3))


def _apply_vector(cells, x, y, z):
    return tuple(cells[r * 4] * x + cells[r * 4 + 1] * y + cells[r * 4 + 2] * z for r in range(3))


def _translation(dx, dy, dz):
    m = _identity()
    m[3], m[7], m[11] = float(dx), float(dy), float(dz)
    return m


def _rotation(angle, axis, origin):
    """Rodrigues: rotation by ``angle`` radians about the unit ``axis`` through ``origin``."""
    ux, uy, uz = axis
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    r = [[c + ux * ux * t, ux * uy * t - uz * s, ux * uz * t + uy * s],
         [uy * ux * t + uz * s, c + uy * uy * t, uy * uz * t - ux * s],
         [uz * ux * t - uy * s, uz * uy * t + ux * s, c + uz * uz * t]]
    ox, oy, oz = origin
    moved = [r[i][0] * ox + r[i][1] * oy + r[i][2] * oz for i in range(3)]
    cells = _identity()
    for i in range(3):
        cells[i * 4:i * 4 + 3] = r[i]
        cells[i * 4 + 3] = origin[i] - moved[i]
    return cells


def _invert(cells):
    """Gauss-Jordan inverse of a 4×4; None when singular."""
    n = 4
    a = [list(cells[r * 4:r * 4 + 4]) + [1.0 if r == c else 0.0 for c in range(n)] for r in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(n):
            if r != col and a[r][col] != 0.0:
                f = a[r][col]
                a[r] = [rv - f * cv for rv, cv in zip(a[r], a[col])]
    return [a[r][c] for r in range(n) for c in range(n, 2 * n)]


def _det3(c):
    return (c[0] * (c[5] * c[10] - c[6] * c[9])
            - c[1] * (c[4] * c[10] - c[6] * c[8])
            + c[2] * (c[4] * c[9] - c[5] * c[8]))


class _XYZ(Base):
    """Shared by Point3D and Vector3D: three coordinates with the usual accessors."""
    _tolerance = 1e-9

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = float(x), float(y), float(z)

    @classmethod
    def create(cls, x=0.0, y=0.0, z=0.0):
        return cls(x, y, z)

    def asArray(self):
        return [self.x, self.y, self.z]

    def setWithArray(self, coordinates):
        self.x, self.y, self.z = (float(v) for v in coordinates)
        return True

    def set(self, x, y, z):
        self.x, self.y, self.z = float(x), float(y), float(z)
        return True

    def getData(self):
        return True, self.x, self.y, self.z

    def copy(self):
        return type(self)(self.x, self.y, self.z)

    def isEqualTo(self, other):
        return self.isEqualToByTolerance(other, self._tolerance)

    def isEqualToByTolerance(self, other, tolerance):
        return (abs(self.x - other.x) <= tolerance and abs(self.y - other.y) <= tolerance
                and abs(self.z - other.z) <= tolerance)

    def __repr__(self):
        return f'{type(self).__name__}({self.x:g}, {self.y:g}, {self.z:g})'


class Point3D(_XYZ):
    def transformBy(self, matrix):
        self.x, self.y, self.z = _apply_point(matrix._cells, self.x, self.y, self.z)
        return True

    def distanceTo(self, point):
        return math.sqrt((self.x - point.x) ** 2 + (self.y - point.y) ** 2 + (self.z - point.z) ** 2)

    def vectorTo(self, point):
        return Vector3D(point.x - self.x, point.y - self.y, point.z - self.z)

    def translateBy(self, vector):
        self.x, self.y, self.z = self.x + vector.x, self.y + vector.y, self.z + vector.z
        return True

    def asVector(self):
        return Vector3D(self.x, self.y, self.z)


class Vector3D(_XYZ):
    def transformBy(self, matrix):
        # A vector is a direction: the translation column does not apply.
        self.x, self.y, self.z = _apply_vector(matrix._cells, self.x, self.y, self.z)
        return True

    @property
    def length(self):
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)

    def normalize(self):
        n = self.length
        if n == 0.0:
            return False
        self.x, self.y, self.z = self.x / n, self.y / n, self.z / n
        return True

    def add(self, vector):
        self.x, self.y, self.z = self.x + vector.x, self.y + vector.y, self.z + vector.z
        return True

    def subtract(self, vector):
        self.x, self.y, self.z = self.x - vector.x, self.y - vector.y, self.z - vector.z
        return True

    def scaleBy(self, scale):
        self.x, self.y, self.z = self.x * scale, self.y * scale, self.z * scale
        return True

    def dotProduct(self, vector):
        return self.x * vector.x + self.y * vector.y + self.z * vector.z

    def crossProduct(self, vector):
        return Vector3D(self.y * vector.z - self.z * vector.y,
                        self.z * vector.x - self.x * vector.z,
                        self.x * vector.y - self.y * vector.x)

    def angleTo(self, vector):
        denominator = self.length * vector.length
        if denominator == 0.0:
            return 0.0
        return math.acos(max(-1.0, min(1.0, self.dotProduct(vector) / denominator)))

    def isParallelTo(self, vector):
        return self.crossProduct(vector).length <= self._tolerance * max(1.0, self.length * vector.length)

    def isPerpendicularTo(self, vector):
        return abs(self.dotProduct(vector)) <= self._tolerance * max(1.0, self.length * vector.length)

    def asPoint(self):
        return Point3D(self.x, self.y, self.z)


class Matrix3D(Base):
    def __init__(self):
        self._cells = _identity()

    @staticmethod
    def create():
        return Matrix3D()

    def setToIdentity(self):
        self._cells = _identity()
        return True

    def asArray(self):
        return list(self._cells)

    def setWithArray(self, cells):
        cells = [float(v) for v in cells]
        if len(cells) != 16:
            raise ValueError('Matrix3D.setWithArray wants 16 cells')
        self._cells = cells
        return True

    def getCell(self, row, column):
        return self._cells[row * 4 + column]

    def setCell(self, row, column, value):
        self._cells[row * 4 + column] = float(value)
        return True

    def copy(self):
        m = Matrix3D()
        m._cells = list(self._cells)
        return m

    def transformBy(self, matrix):
        """self = matrix · self: ``matrix`` is applied after what self already does."""
        self._cells = _multiply(matrix._cells, self._cells)
        return True

    def invert(self):
        inverse = _invert(self._cells)
        if inverse is None:
            return False
        self._cells = inverse
        return True

    @property
    def determinant(self):
        return _det3(self._cells)

    @property
    def translation(self):
        return Vector3D(self._cells[3], self._cells[7], self._cells[11])

    @translation.setter
    def translation(self, value):
        self._cells[3], self._cells[7], self._cells[11] = float(value.x), float(value.y), float(value.z)

    def isEqualTo(self, matrix):
        return all(abs(a - b) <= 1e-9 for a, b in zip(self._cells, matrix._cells))

    def setToRotation(self, angle, axis, origin):
        """Rotation by ``angle`` radians about ``axis`` through ``origin``."""
        unit = axis.copy()
        if not unit.normalize():
            raise RuntimeError('3 : invalid argument axis')
        self._cells = _rotation(float(angle), (unit.x, unit.y, unit.z), (origin.x, origin.y, origin.z))
        return True

    def setWithCoordinateSystem(self, origin, xAxis, yAxis, zAxis):
        self._cells = [xAxis.x, yAxis.x, zAxis.x, origin.x,
                       xAxis.y, yAxis.y, zAxis.y, origin.y,
                       xAxis.z, yAxis.z, zAxis.z, origin.z,
                       0.0, 0.0, 0.0, 1.0]
        return True

    def getAsCoordinateSystem(self):
        c = self._cells
        return (Point3D(c[3], c[7], c[11]), Vector3D(c[0], c[4], c[8]),
                Vector3D(c[1], c[5], c[9]), Vector3D(c[2], c[6], c[10]))

    def __repr__(self):
        rows = [' '.join(f'{v:g}' for v in self._cells[r * 4:r * 4 + 4]) for r in range(4)]
        return 'Matrix3D[' + '; '.join(rows) + ']'


class BoundingBox3D(Base):
    def __init__(self, minPoint, maxPoint):
        self.minPoint, self.maxPoint = minPoint.copy(), maxPoint.copy()

    @staticmethod
    def create(minPoint, maxPoint):
        return BoundingBox3D(minPoint, maxPoint)

    def copy(self):
        return BoundingBox3D(self.minPoint, self.maxPoint)

    def contains(self, point):
        return (self.minPoint.x <= point.x <= self.maxPoint.x and self.minPoint.y <= point.y <= self.maxPoint.y
                and self.minPoint.z <= point.z <= self.maxPoint.z)

    def expand(self, point):
        self.minPoint = Point3D(min(self.minPoint.x, point.x), min(self.minPoint.y, point.y),
                                min(self.minPoint.z, point.z))
        self.maxPoint = Point3D(max(self.maxPoint.x, point.x), max(self.maxPoint.y, point.y),
                                max(self.maxPoint.z, point.z))
        return True

    def combine(self, boundingBox):
        self.expand(boundingBox.minPoint)
        self.expand(boundingBox.maxPoint)
        return True

    def intersects(self, boundingBox):
        return all(getattr(self.minPoint, a) <= getattr(boundingBox.maxPoint, a)
                   and getattr(boundingBox.minPoint, a) <= getattr(self.maxPoint, a) for a in 'xyz')


class Line3D(Base):
    def __init__(self, startPoint, endPoint):
        self.startPoint, self.endPoint = startPoint.copy(), endPoint.copy()

    @staticmethod
    def create(startPoint, endPoint):
        return Line3D(startPoint, endPoint)

    def copy(self):
        return Line3D(self.startPoint, self.endPoint)

    def getData(self):
        return True, self.startPoint.copy(), self.endPoint.copy()

    def set(self, startPoint, endPoint):
        self.startPoint, self.endPoint = startPoint.copy(), endPoint.copy()
        return True

    def asInfiniteLine(self):
        direction = self.startPoint.vectorTo(self.endPoint)
        direction.normalize()
        return InfiniteLine3D(self.startPoint, direction)


class InfiniteLine3D(Base):
    def __init__(self, origin, direction):
        self.origin, self.direction = origin.copy(), direction.copy()

    @staticmethod
    def create(origin, direction):
        return InfiniteLine3D(origin, direction)

    def copy(self):
        return InfiniteLine3D(self.origin, self.direction)

    def getData(self):
        return True, self.origin.copy(), self.direction.copy()

    def set(self, origin, direction):
        self.origin, self.direction = origin.copy(), direction.copy()
        return True


class Circle3D(Base):
    def __init__(self, center, normal, radius):
        self.center, self.normal, self.radius = center.copy(), normal.copy(), float(radius)

    @staticmethod
    def createByCenter(center, normal, radius):
        return Circle3D(center, normal, radius)

    def copy(self):
        return Circle3D(self.center, self.normal, self.radius)

    def getData(self):
        return True, self.center.copy(), self.normal.copy(), self.radius

    def set(self, center, normal, radius):
        self.center, self.normal, self.radius = center.copy(), normal.copy(), float(radius)
        return True
