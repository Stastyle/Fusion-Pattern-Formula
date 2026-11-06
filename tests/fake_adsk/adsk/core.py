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

    def __iter__(self):
        return iter(self._items)


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
