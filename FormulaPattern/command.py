"""Fusion 360 side of the Formula Pattern add-in: toolbar button, dialog, feature creation.

Everything that does not need Fusion (formula evaluation, presets, preview text)
lives in engine.py so it can be tested outside Fusion.

How the result is built
-----------------------
For every copy k the add-in creates one *native* Rectangular (or Circular)
Pattern feature with quantity 2 and extent (or total angle) equal to the
formula's value for that copy. That yields the original plus one copy at
exactly the computed position, works for faces, bodies, features and
components alike, keeps cuts behaving as cuts, and leaves every distance as
an ordinary editable model parameter. All features are collected into one
timeline group.
"""
import json
import math
import os
import re
import time
import traceback
import uuid

import adsk.core
import adsk.fusion

from . import engine
from .settings import load_settings, save_settings

# ---------------------------------------------------------------------------
# Identifiers and constants
# ---------------------------------------------------------------------------

CMD_ID = 'stasm_FormulaPattern'
CMD_NAME = 'Formula Pattern'
CMD_TOOLTIP = ('Pattern objects along a direction or around an axis where a formula decides the '
               'distance (or angle) of every copy: guitar frets, growing gaps, easing curves, '
               'Fibonacci … Pick a preset or write your own formula.')

WORKSPACE_ID = 'FusionSolidEnvironment'
TAB_ID = 'SolidTab'
PANEL_ID = 'SolidCreatePanel'
SKETCH_TAB_ID = 'SketchTab'
SKETCH_PANEL_ID = 'SketchCreatePanel'
# The button goes right after the native pattern drop-down if one of these ids exists.
# Native controls to sit next to, first one found wins (ids from Fusion's TabToolbars.xml;
# the extra solid candidates cover older UI layouts). Not found → appended to the panel.
PATTERN_CONTROL_IDS = ('PatternRectangular', 'PatternDropDown', 'FusionPatternDropDownCommand',
                       'FusionRectangularPatternCommand')
SKETCH_PATTERN_CONTROL_IDS = ('RectangularSketchPatternCommand', 'CircularSketchPatternCommand')
PANELS = (
    (TAB_ID, PANEL_ID, PATTERN_CONTROL_IDS),
    (SKETCH_TAB_ID, SKETCH_PANEL_ID, SKETCH_PATTERN_CONTROL_IDS),
)

RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', 'FormulaPattern')
ATTRIBUTE_GROUP = 'FormulaPattern'
MAX_MODEL_PREVIEW = 100          # above this many copies the model preview is skipped (numbers still shown)
DEFAULT_GROUP_NAME = 'Formula Pattern'

# Command input ids
ID_TYPE = 'fpPatternType'
ID_OBJECT_TYPE = 'fpObjectType'
ID_OBJECTS = 'fpObjects'
ID_DIRECTION = 'fpDirection'
ID_AXIS = 'fpAxis'
ID_FLIP = 'fpFlip'
ID_COUNT = 'fpCount'
ID_FORMULA_GROUP = 'fpFormulaGroup'
ID_PRESET = 'fpPreset'
ID_MODE = 'fpMode'
ID_FORMULA = 'fpFormula'
ID_A_LEN = 'fpALength'
ID_A_ANG = 'fpAAngle'
ID_B = 'fpB'
ID_C = 'fpC'
ID_NOTES = 'fpNotes'
ID_PREVIEW_GROUP = 'fpPreviewGroup'
ID_PREVIEW = 'fpPreview'
ID_LIVE = 'fpLivePreview'
ID_REFERENCE_GROUP = 'fpReferenceGroup'
ID_REFERENCE = 'fpReference'
ID_ADVANCED_GROUP = 'fpAdvancedGroup'
ID_COMPUTE = 'fpCompute'
ID_GROUP_NAME = 'fpGroupName'
ID_EDIT_GROUP = 'fpEditGroup'
ID_PATTERN_LIST = 'fpPatternList'
ID_EXISTING = 'fpExisting'
ID_EDIT_STATUS = 'fpEditStatus'
NEW_PATTERN_LABEL = 'New pattern'

OBJECT_SKETCH = 'Sketch curves'
OBJECT_TYPES = (
    ('Bodies', ('SolidBodies', 'SurfaceBodies')),
    ('Features', ('Features',)),
    ('Faces', ('Faces',)),
    ('Components', ('Occurrences',)),
    (OBJECT_SKETCH, ('SketchCurves', 'SketchPoints')),
)
DIRECTION_FILTERS = ('LinearEdges', 'ConstructionLines', 'SketchLines')
AXIS_FILTERS = ('LinearEdges', 'ConstructionLines', 'SketchLines', 'CircularEdges', 'SketchCircles',
                'CylindricalFaces', 'ConicalFaces', 'ToroidalFaces')
# Sketch patterns are driven by sketch geometry only: a line for the direction (or none for
# the sketch X axis) and a point / circle centre for the centre of a circular pattern.
SKETCH_DIRECTION_FILTERS = ('SketchLines',)
SKETCH_AXIS_FILTERS = ('SketchPoints', 'SketchCircles')
# A saved pattern is picked through any of its copies: a pattern feature (clicked in the
# timeline, or through a face it made) or a patterned sketch curve / point.
EXISTING_FILTERS = ('Features', 'SketchCurves', 'SketchPoints')
PROMPTS = {
    'objects': 'Select the objects to pattern',
    'objects_sketch': 'Select the sketch curves or points to pattern',
    'direction': 'Select a linear edge, sketch line or construction axis',
    'direction_sketch': 'Select a sketch line, or leave empty to use the sketch X axis',
    'axis': 'Select an axis: edge, circle, cylindrical face, sketch line or construction axis',
    'axis_sketch': 'Select the centre: a sketch point or a circle',
    'copies': 'The copies of the chosen pattern. Click any copy of a Formula Pattern to pick that pattern',
}
# Internal-unit values the value inputs are created with (see Dialog._number).
NUMBER_DEFAULTS = {ID_A_LEN: 1.0, ID_A_ANG: math.radians(15.0), ID_B: 0.0, ID_C: 0.0}
COMPUTE_OPTIONS = (
    ('Optimized', adsk.fusion.PatternComputeOptions.OptimizedPatternCompute),
    ('Identical', adsk.fusion.PatternComputeOptions.IdenticalPatternCompute),
    ('Adjust', adsk.fusion.PatternComputeOptions.AdjustPatternCompute),
)
LENGTH_UNITS = {'mm', 'cm', 'm', 'km', 'in', 'ft', 'yd', 'mi', 'mil', 'micron', 'um', 'µm', 'nmi'}
ANGLE_UNITS = {'deg', 'rad', 'grad'}
# Fusion spells the document unit according to a user preference (inch can come back as
# "in", "inch" or '"'); map every spelling to the short form the API accepts everywhere.
UNIT_SPELLINGS = {
    'mm': 'mm', 'millimeter': 'mm', 'millimetre': 'mm',
    'cm': 'cm', 'centimeter': 'cm', 'centimetre': 'cm',
    'm': 'm', 'meter': 'm', 'metre': 'm',
    'in': 'in', 'inch': 'in', '"': 'in',
    'ft': 'ft', 'foot': 'ft', 'feet': 'ft', "'": 'ft',
}
DISTANCE_UNIT_NAMES = {
    adsk.fusion.DistanceUnits.MillimeterDistanceUnits: 'mm',
    adsk.fusion.DistanceUnits.CentimeterDistanceUnits: 'cm',
    adsk.fusion.DistanceUnits.MeterDistanceUnits: 'm',
    adsk.fusion.DistanceUnits.InchDistanceUnits: 'in',
    adsk.fusion.DistanceUnits.FootDistanceUnits: 'ft',
}


def document_length_unit(design) -> str:
    """The design's length unit as a short string ('mm', 'in', …) independent of unit-symbol preferences."""
    raw = ''
    try:
        raw = (design.unitsManager.defaultLengthUnits or '').strip()
    except Exception:
        pass
    if raw.lower() in UNIT_SPELLINGS:
        return UNIT_SPELLINGS[raw.lower()]
    try:
        return DISTANCE_UNIT_NAMES[int(design.fusionUnitsManager.distanceDisplayUnits)]
    except Exception:
        return raw or 'mm'

_app = None
_ui = None
_handlers = []   # event handlers must stay referenced or Fusion drops them


# ---------------------------------------------------------------------------
# Add-in start / stop
# ---------------------------------------------------------------------------

def start():
    global _app, _ui
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    if LOG_FILE:
        try:
            open(LOG_FILE, 'w', encoding='utf-8').close()
        except Exception:
            pass

    cmd_def = _ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    cmd_def = _ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP, RESOURCES)

    on_created = CommandCreatedHandler()
    cmd_def.commandCreated.add(on_created)
    _handlers.append(on_created)

    added = 0
    for tab_id, panel_id, neighbours in PANELS:
        panel = _find_panel(tab_id, panel_id)
        if panel is None:
            continue
        existing = panel.controls.itemById(CMD_ID)
        if existing:
            existing.deleteMe()
        position = next((pid for pid in neighbours if panel.controls.itemById(pid)), '')
        if position:
            control = panel.controls.addCommand(cmd_def, position, False)
        else:
            control = panel.controls.addCommand(cmd_def)
        control.isPromotedByDefault = True
        control.isPromoted = True
        added += 1
    if not added:
        _ui.messageBox('Formula Pattern: could not find the Solid > Create or Sketch > Create panel. '
                       'The command is still available from the command search (press S).')


def stop():
    for tab_id, panel_id, _ in PANELS:
        panel = _find_panel(tab_id, panel_id)
        if panel:
            control = panel.controls.itemById(CMD_ID)
            if control:
                control.deleteMe()
    cmd_def = _ui.commandDefinitions.itemById(CMD_ID) if _ui else None
    if cmd_def:
        cmd_def.deleteMe()
    _handlers.clear()


def _find_panel(tab_id: str, panel_id: str):
    try:
        workspace = _ui.workspaces.itemById(WORKSPACE_ID)
        tab = workspace.toolbarTabs.itemById(tab_id)
        panel = tab.toolbarPanels.itemById(panel_id)
        if panel:
            return panel
    except Exception:
        pass
    try:
        return _ui.allToolbarPanels.itemById(panel_id)
    except Exception:
        return None


def _report(what: str):
    _log(f'{what}:\n{traceback.format_exc()}')
    if _ui:
        _ui.messageBox(f'Formula Pattern: {what}\n\n{traceback.format_exc()}')


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

LOG_FILE = os.path.join(os.path.expanduser('~'), '.fusion_formula_pattern.log')


def _log(message: str):
    """Diagnostics for what can only be seen inside Fusion. Appended to LOG_FILE (None: off);
    start() empties the file."""
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f'{time.strftime("%H:%M:%S")} {message}\n')
    except Exception:
        pass


class Dialog:
    """Owns the command inputs and knows how to read them, preview them and build the result."""

    def __init__(self, cmd: adsk.core.Command):
        self.cmd = cmd
        self.inputs = cmd.commandInputs
        self.design = adsk.fusion.Design.cast(_app.activeProduct)
        if not self.design:
            raise RuntimeError('Formula Pattern needs an open design')
        self.units = self.design.unitsManager
        self.length_unit = document_length_unit(self.design)
        self.active_sketch = self._active_sketch()
        self.preset_ids = []          # id of each row of the preset drop-down, in order
        self.last_error = None
        self.editing = None           # PatternRecord of the saved pattern being edited, if any
        self.dirty = False            # something changed since the saved pattern was loaded
        self.patterns = []            # PatternRecords offered in the "Saved pattern" list, in order
        self._pending = []            # selections Fusion did not take yet: (input id, entities, tries)
        self.reselect_needed = False  # Fusion kept refusing them: the user has to click them
        self._loading = False         # choose_pattern() is filling the inputs
        self.saved = load_settings()
        self._build()
        self._apply_settings(self.saved)
        # The value inputs a, b, c are only evaluated by Fusion once the dialog is on screen
        # (reading a unitless one before that raises InternalValidationError), so restoring
        # their saved text and the first preview wait for the activate event: on_activate().

    def on_activate(self):
        """Fusion has put the dialog on screen; every input can be read and written now.

        Also fires when the command is re-activated after a nested command, so the
        saved numbers are applied only the first time.
        """
        saved, self.saved = self.saved, {}
        self._apply_number_settings(saved)
        try:
            # The first selection input in the dialog would get the focus otherwise, and that is
            # now the hidden Copies input of the edit group: clicks must go to Objects.
            adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS)).hasFocus = True
        except Exception:
            pass
        self.refresh_preview()

    @staticmethod
    def _active_sketch():
        try:
            return adsk.fusion.Sketch.cast(_app.activeEditObject)
        except Exception:
            return None

    # ---- construction ----

    def _build(self):
        inputs = self.inputs

        # New pattern, or one of the saved ones? That is the first choice, so it comes first.
        group = inputs.addGroupCommandInput(ID_EDIT_GROUP, 'Edit a saved pattern')
        group.isExpanded = True
        try:
            self.patterns = list_patterns(self.design, self.active_sketch)
        except Exception:
            self.patterns = []
        dd = group.children.addDropDownCommandInput(ID_PATTERN_LIST, 'Saved pattern',
                                                    adsk.core.DropDownStyles.TextListDropDownStyle)
        dd.listItems.add(NEW_PATTERN_LABEL, True)
        for record in self.patterns:
            dd.listItems.add(record.label, False)
        dd.tooltip = ('Every pattern made by Formula Pattern in this design. Pick one to load its objects, '
                      'direction, formula and values and to highlight its copies; OK then replaces it in '
                      'place. "New pattern" makes a new one.')
        sel = group.children.addSelectionInput(ID_EXISTING, 'Copies', PROMPTS['copies'])
        self._set_filters(sel, EXISTING_FILTERS)
        sel.setSelectionLimits(0, 0)
        sel.isVisible = False
        sel.tooltip = ('The copies of the chosen pattern, highlighted in the model and the timeline. '
                       'Clicking any copy of a Formula Pattern here (a feature in its timeline group, a '
                       'face it created, or a patterned sketch curve) picks that pattern instead.')
        status = group.children.addTextBoxCommandInput(ID_EDIT_STATUS, '', '', 2, True)
        status.isFullWidth = True
        status.isVisible = False

        dd = inputs.addDropDownCommandInput(ID_TYPE, 'Pattern type', adsk.core.DropDownStyles.TextListDropDownStyle)
        dd.listItems.add('Linear', True)
        dd.listItems.add('Circular', False)
        dd.tooltip = 'Linear: copies along a direction. Circular: copies around an axis.'

        default_type = OBJECT_SKETCH if self.active_sketch else 'Bodies'
        dd = inputs.addDropDownCommandInput(ID_OBJECT_TYPE, 'Object type', adsk.core.DropDownStyles.TextListDropDownStyle)
        for name, _ in OBJECT_TYPES:
            dd.listItems.add(name, name == default_type)
        dd.tooltip = ('What kind of objects to pattern (all selected objects must be of this kind). '
                      '"Sketch curves" patterns inside a sketch, like the native sketch patterns.')

        sel = inputs.addSelectionInput(ID_OBJECTS, 'Objects', PROMPTS['objects'])
        sel.setSelectionLimits(1, 0)

        sel = inputs.addSelectionInput(ID_DIRECTION, 'Direction', PROMPTS['direction'])
        sel.tooltip = 'Copies move along this. Use "Flip direction" if they go the wrong way.'

        sel = inputs.addSelectionInput(ID_AXIS, 'Axis', PROMPTS['axis'])
        sel.isVisible = False
        sel.tooltip = 'Copies rotate around this. Use "Flip direction" to rotate the other way.'

        flip = inputs.addBoolValueInput(ID_FLIP, 'Flip direction', True, '', False)
        flip.tooltip = 'Mirror the pattern to the other side of the original.'

        count = inputs.addIntegerSpinnerCommandInput(ID_COUNT, 'Copies', 1, 1000, 1, 10)
        count.tooltip = 'How many new copies to create. The original stays where it is and is copy 0; the formula runs for i = 1 … Copies.'

        group = inputs.addGroupCommandInput(ID_FORMULA_GROUP, 'Formula')
        group.isExpanded = True
        g = group.children

        dd = g.addDropDownCommandInput(ID_PRESET, 'Preset', adsk.core.DropDownStyles.TextListDropDownStyle)
        dd.tooltip = 'Ready-made formulas. Picking one fills in the formula and a, b, c; you can edit them afterwards.'
        self._fill_presets(circular=False, select_id='custom')

        dd = g.addDropDownCommandInput(ID_MODE, 'Formula gives', adsk.core.DropDownStyles.TextListDropDownStyle)
        dd.tooltip = ('"Distance from original": the formula is where copy i sits, measured from the original. '
                      '"Gap to previous": the formula is the step from copy i-1 to copy i; positions add up.')
        self._fill_modes(circular=False, mode=engine.MODE_POSITION)

        formula = g.addStringValueInput(ID_FORMULA, 'Formula', 'a * i')
        formula.tooltip = 'Math in i (copy number), n (copies), t = i/n, a, b, c and any user parameter. ^ is power. See Formula reference below.'

        a_len = g.addValueInput(ID_A_LEN, 'a', self.length_unit, adsk.core.ValueInput.createByReal(1.0))
        a_len.tooltip = 'Value for a in the formula (a length). Accepts expressions and units, e.g. 25.5 in.'
        a_ang = g.addValueInput(ID_A_ANG, 'a', 'deg', adsk.core.ValueInput.createByReal(math.radians(15.0)))
        a_ang.tooltip = 'Value for a in the formula (an angle).'
        a_ang.isVisible = False
        b = g.addValueInput(ID_B, 'b', '', adsk.core.ValueInput.createByReal(0.0))
        b.tooltip = 'Plain number for b in the formula (document units where used as a length).'
        c = g.addValueInput(ID_C, 'c', '', adsk.core.ValueInput.createByReal(0.0))
        c.tooltip = 'Plain number for c in the formula.'

        notes = g.addTextBoxCommandInput(ID_NOTES, '', engine.preset_by_id('custom').notes, 3, True)
        notes.isFullWidth = True

        group = inputs.addGroupCommandInput(ID_PREVIEW_GROUP, 'Preview')
        group.isExpanded = False          # opened by refresh_preview() when there is an error to see
        preview = group.children.addTextBoxCommandInput(ID_PREVIEW, '', '', 9, True)
        preview.isFullWidth = True
        live = group.children.addBoolValueInput(ID_LIVE, 'Show in model', True, '', True)
        live.tooltip = f'Build the copies while the dialog is open. Turned off automatically above {MAX_MODEL_PREVIEW} copies.'

        group = inputs.addGroupCommandInput(ID_REFERENCE_GROUP, 'Formula reference')
        group.isExpanded = False
        ref = group.children.addTextBoxCommandInput(ID_REFERENCE, '', engine.REFERENCE_HTML, 10, True)
        ref.isFullWidth = True

        group = inputs.addGroupCommandInput(ID_ADVANCED_GROUP, 'Advanced')
        group.isExpanded = False
        dd = group.children.addDropDownCommandInput(ID_COMPUTE, 'Compute', adsk.core.DropDownStyles.TextListDropDownStyle)
        for k, (name, _) in enumerate(COMPUTE_OPTIONS):
            dd.listItems.add(name, k == 0)
        dd.tooltip = 'Same as the native pattern: Optimized is fastest, Identical assumes identical copies, Adjust recomputes each copy against the model.'
        name = group.children.addStringValueInput(ID_GROUP_NAME, 'Timeline group', DEFAULT_GROUP_NAME)
        name.tooltip = 'Name of the timeline group that holds the copies.'

        self._update_selection_inputs()

    def _update_selection_inputs(self):
        """Visibility, filters and limits of the Objects / Direction / Axis inputs for the current
        pattern type and object type."""
        circular, sketch = self.circular, self.sketch_mode
        objects = adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS))
        direction = adsk.core.SelectionCommandInput.cast(self._input(ID_DIRECTION))
        axis = adsk.core.SelectionCommandInput.cast(self._input(ID_AXIS))

        self._set_filters(objects, dict(OBJECT_TYPES)[self.object_type])
        objects.commandPrompt = PROMPTS['objects_sketch' if sketch else 'objects']

        direction.isVisible = not circular
        axis.isVisible = circular
        # A hidden input must not demand a selection; a sketch direction is optional (X axis).
        direction.setSelectionLimits(0 if (circular or sketch) else 1, 1)
        axis.setSelectionLimits(1 if circular else 0, 1)
        self._set_filters(direction, SKETCH_DIRECTION_FILTERS if sketch else DIRECTION_FILTERS)
        self._set_filters(axis, SKETCH_AXIS_FILTERS if sketch else AXIS_FILTERS)
        direction.commandPrompt = PROMPTS['direction_sketch' if sketch else 'direction']
        axis.commandPrompt = PROMPTS['axis_sketch' if sketch else 'axis']

        self._input(ID_A_LEN).isVisible = not circular
        self._input(ID_A_ANG).isVisible = circular
        self._input(ID_ADVANCED_GROUP).isVisible = not sketch   # compute option / timeline group: features only

    @staticmethod
    def _set_filters(sel: adsk.core.SelectionCommandInput, filters):
        sel.clearSelectionFilter()
        for f in filters:
            sel.addSelectionFilter(f)

    def _fill_presets(self, circular: bool, select_id: str):
        dd = adsk.core.DropDownCommandInput.cast(self.inputs.itemById(ID_PRESET))
        dd.listItems.clear()
        self.preset_ids = []
        presets = engine.presets_for(circular)
        if select_id not in [p.id for p in presets]:
            select_id = 'custom'
        for p in presets:
            dd.listItems.add(p.name, p.id == select_id)
            self.preset_ids.append(p.id)

    def _fill_modes(self, circular: bool, mode: str):
        dd = adsk.core.DropDownCommandInput.cast(self.inputs.itemById(ID_MODE))
        dd.listItems.clear()
        if circular:
            labels = ('Angle from original', 'Angle step from previous')
        else:
            labels = ('Distance from original', 'Gap to previous')
        dd.listItems.add(labels[0], mode == engine.MODE_POSITION)
        dd.listItems.add(labels[1], mode == engine.MODE_SPACING)

    # ---- reading inputs ----

    def _input(self, input_id):
        return self.inputs.itemById(input_id)

    @property
    def circular(self) -> bool:
        return adsk.core.DropDownCommandInput.cast(self._input(ID_TYPE)).selectedItem.index == 1

    @property
    def object_type(self) -> str:
        return adsk.core.DropDownCommandInput.cast(self._input(ID_OBJECT_TYPE)).selectedItem.name

    @property
    def mode(self) -> str:
        index = adsk.core.DropDownCommandInput.cast(self._input(ID_MODE)).selectedItem.index
        return engine.MODE_SPACING if index == 1 else engine.MODE_POSITION

    @property
    def preset_id(self) -> str:
        item = adsk.core.DropDownCommandInput.cast(self._input(ID_PRESET)).selectedItem
        if item is None or item.index >= len(self.preset_ids):
            return 'custom'
        return self.preset_ids[item.index]

    @property
    def formula_text(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA)).value

    @property
    def count(self) -> int:
        return adsk.core.IntegerSpinnerCommandInput.cast(self._input(ID_COUNT)).value

    @property
    def flip(self) -> bool:
        return adsk.core.BoolValueCommandInput.cast(self._input(ID_FLIP)).value

    @property
    def live(self) -> bool:
        return adsk.core.BoolValueCommandInput.cast(self._input(ID_LIVE)).value

    @property
    def group_name(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_GROUP_NAME)).value.strip() or DEFAULT_GROUP_NAME

    @property
    def compute_option(self):
        index = adsk.core.DropDownCommandInput.cast(self._input(ID_COMPUTE)).selectedItem.index
        return COMPUTE_OPTIONS[index][1]

    def _number(self, input_id: str, label: str) -> float:
        inp = adsk.core.ValueCommandInput.cast(self._input(input_id))
        try:
            if inp.isValidExpression:
                return inp.value
            text = inp.expression
        except Exception:
            # Fusion has not evaluated this input yet: unitless value inputs only get a value
            # once the dialog is on screen. Until then, use what the input was created with.
            return NUMBER_DEFAULTS[input_id]
        raise engine.FormulaError(f"'{label}' is not a valid number: {text}")

    @property
    def sketch_mode(self) -> bool:
        return self.object_type == OBJECT_SKETCH

    @property
    def a(self) -> float:
        if self.circular:
            return math.degrees(self._number(ID_A_ANG, 'a'))      # internal angles are radians
        return self.units.convert(self._number(ID_A_LEN, 'a'), 'cm', self.length_unit)   # internal lengths are cm

    @property
    def b(self) -> float:
        return self._number(ID_B, 'b')

    @property
    def c(self) -> float:
        return self._number(ID_C, 'c')

    def user_parameter_vars(self) -> dict:
        """User parameters of the design, by name, in document units (lengths) or degrees (angles)."""
        values = {}
        try:
            for p in self.design.userParameters:
                name = p.name
                if not name.isidentifier():
                    continue
                unit = (p.unit or '').strip()
                try:
                    if unit in ANGLE_UNITS:
                        values[name] = math.degrees(p.value)
                    elif unit in LENGTH_UNITS:
                        values[name] = self.units.convert(p.value, 'cm', self.length_unit)
                    else:
                        values[name] = float(p.value)
                except Exception:
                    continue
        except Exception:
            pass
        return values

    def spec(self) -> engine.PatternSpec:
        return engine.PatternSpec(
            formula=self.formula_text, count=self.count, mode=self.mode, circular=self.circular,
            flip=self.flip, a=self.a, b=self.b, c=self.c, extra_vars=self.user_parameter_vars())

    def reference_entity(self):
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_AXIS if self.circular else ID_DIRECTION))
        return sel.selection(0).entity if sel.selectionCount == 1 else None

    def selected_objects(self) -> adsk.core.ObjectCollection:
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS))
        collection = adsk.core.ObjectCollection.create()
        for k in range(sel.selectionCount):
            collection.add(sel.selection(k).entity)
        return collection

    # ---- reacting to changes ----

    def on_input_changed(self, changed: adsk.core.CommandInput):
        cid = changed.id
        if cid == ID_PATTERN_LIST:
            self.on_pattern_list_changed()
        elif cid == ID_EXISTING:
            self.on_existing_changed()
        elif cid == ID_TYPE:
            self.dirty = True
            self.on_type_changed()
        elif cid == ID_OBJECT_TYPE:
            self.dirty = True
            self.on_object_type_changed()
            return
        elif cid == ID_PRESET:
            self.dirty = True
            self.apply_preset(self.preset_id)
        elif cid in (ID_FORMULA, ID_MODE):
            self.dirty = True
            self._switch_to_custom_if_edited()
        elif cid in (ID_COUNT, ID_A_LEN, ID_A_ANG, ID_B, ID_C, ID_FLIP, ID_OBJECTS, ID_DIRECTION, ID_AXIS):
            self.dirty = True
        elif cid in (ID_COMPUTE, ID_GROUP_NAME):
            self.dirty = True
            return
        elif cid != ID_LIVE:
            return
        self.refresh_preview()

    def on_pattern_list_changed(self):
        dd = adsk.core.DropDownCommandInput.cast(self._input(ID_PATTERN_LIST))
        index = dd.selectedItem.index if dd.selectedItem is not None else 0
        self.choose_pattern(self.patterns[index - 1] if 1 <= index <= len(self.patterns) else None)

    def on_existing_changed(self):
        """The Copies input: a copy clicked picks its pattern; cleared by the user drops the edit."""
        if self._loading:
            return                    # our own filling of the input, not a click
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING))
        if sel.selectionCount == 0:
            if self.editing is not None or self.last_error:
                self.choose_pattern(None)
            return
        entity = sel.selection(sel.selectionCount - 1).entity     # the one just clicked
        try:
            record = find_pattern(self.design, entity, self.patterns)
        except NotAPattern as e:
            self.last_error = str(e)      # say so, keep whatever is being edited
            self._restore_highlight()
            return
        if self.editing is not None and record.id == self.editing.id:
            self._restore_highlight()     # a copy of the loaded pattern: keep the user's changes
            return
        self.choose_pattern(record)

    def _restore_highlight(self):
        """Put the Copies input back to the loaded pattern's copies after a click into it."""
        self._loading = True
        try:
            if self.editing is not None:
                self._fill_selection(ID_EXISTING, self.editing.highlight)
            else:
                adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING)).clearSelection()
            self._focus_objects()
        finally:
            self._loading = False

    def choose_pattern(self, record):
        """Edit ``record`` (None: make a new pattern). Selects it in the list, loads it into the
        dialog and highlights its copies through the Copies input. Nothing is rebuilt until the
        user changes something (see ``unchanged``)."""
        self._loading = True
        try:
            _log(f'choose_pattern: {record.label if record else None}')
            if record is not None:
                _log(f'  kind={record.kind} copies={len(record.copies)} highlight={len(record.highlight)} '
                     f'objects={[_describe(e) for e in record.objects]} '
                     f'reference={_describe(record.reference) if record.reference is not None else None}')
            self.last_error = None
            self.editing = None
            self.dirty = False
            self._pending = []
            self.reselect_needed = False
            copies = adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING))
            status = self._input(ID_EDIT_STATUS)
            copies.clearSelection()
            self._select_list_entry(record)
            copies.isVisible = status.isVisible = record is not None
            if record is not None:
                read_inputs(self.design, record)
                self._pending = self.load_pattern(record)
                self.editing = record
                _log(f'  pending after load: {[(i, len(e), t) for i, e, t in self._pending]}')
            self._focus_objects()
            self._update_edit_status()
        except Exception:
            _log('choose_pattern failed:\n' + traceback.format_exc())
            raise
        finally:
            self._loading = False

    def _focus_objects(self):
        try:
            adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS)).hasFocus = True
        except Exception:
            pass

    def _complete_pending(self):
        """Retry the selections Fusion did not take while the pattern was loaded (called from
        validateInputs, where the API says addSelection is welcome). After a few refusals the
        user is asked to click them instead."""
        if not self._pending:
            return
        still = []
        for input_id, entities, tries in self._pending:
            missing = self._fill_selection(input_id, entities, clear=False)
            tries += 1
            if missing and tries < 3:
                still.append((input_id, missing, tries))
            elif missing:
                self.reselect_needed = True
        self._pending = still
        self._focus_objects()
        self._update_edit_status()

    def _update_edit_status(self):
        box = adsk.core.TextBoxCommandInput.cast(self._input(ID_EDIT_STATUS))
        if self.editing is None:
            box.formattedText = ''
            return
        text = f'Editing <b>{self.editing.label}</b>. '
        objects_missing = self.selected_objects().count < 1
        reference_missing = self.reference_entity() is None and not (self.sketch_mode and not self.circular)
        if (objects_missing or reference_missing) and not self._pending:
            text += ('Fusion could not re-select its objects or direction. Click its objects and direction '
                     'again, then OK replaces it.')
        elif self.dirty:
            text += 'OK replaces it with these changes.'
        else:
            text += 'Change anything, then OK replaces it. OK now leaves it as it is.'
        box.formattedText = text

    def _select_list_entry(self, record):
        dd = adsk.core.DropDownCommandInput.cast(self._input(ID_PATTERN_LIST))
        if record is None:
            dd.listItems.item(0).isSelected = True
            return
        for k, known in enumerate(self.patterns):
            if known.id == record.id:
                dd.listItems.item(k + 1).isSelected = True
                return
        self.patterns.append(record)          # reached through a click, not listed at opening
        dd.listItems.add(record.label, True)

    @property
    def unchanged(self) -> bool:
        """A saved pattern is loaded and nothing was changed: leave the model as it is."""
        return self.editing is not None and not self.dirty

    def load_pattern(self, record) -> list:
        """Fill the dialog's inputs from a saved pattern. Returns what Fusion did not select,
        as (input id, entities, tries) for _complete_pending()."""
        self._apply_settings(record.settings, force_object_type=True)
        self._apply_number_settings(record.settings)
        design = self.design
        reference = [record.reference] if record.reference is not None else []
        pending = []
        for input_id, entities in ((ID_EXISTING, record.highlight),
                                   (ID_OBJECTS, record.objects),
                                   (ID_AXIS if self.circular else ID_DIRECTION, reference)):
            missing = self._fill_selection(input_id, [_in_root_context(design, e) for e in entities])
            if missing:
                pending.append((input_id, missing, 1))
        return pending

    def _fill_selection(self, input_id, entities, clear=True) -> list:
        """Put ``entities`` into a selection input; returns the ones Fusion did not take."""
        sel = adsk.core.SelectionCommandInput.cast(self._input(input_id))
        if clear:
            sel.clearSelection()
        try:
            sel.hasFocus = True   # addSelection only lands in the input that has focus (Fusion 2026-11)
            _log(f'  {input_id}: focus={sel.hasFocus} visible={sel.isVisible} filters={sel.selectionFilters}')
        except Exception as e:
            _log(f'  {input_id}: hasFocus failed: {e}')
        missing = []
        for e in entities:
            try:
                added = sel.addSelection(e)
                _log(f'  {input_id}: addSelection({_describe(e)}) -> {added}; count now {sel.selectionCount}')
            except Exception as err:
                added = False
                _log(f'  {input_id}: addSelection({_describe(e)}) raised {err}')
            if not added:
                missing.append(e)
        return missing

    def on_type_changed(self):
        circular = self.circular
        self._update_selection_inputs()
        current_preset = self.preset_id
        current_mode = self.mode
        self._fill_presets(circular, current_preset)
        self._fill_modes(circular, current_mode)
        self._show_notes(self.preset_id)

    def on_object_type_changed(self):
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS))
        sel.clearSelection()
        was_sketch = self._input(ID_DIRECTION).selectionFilters == list(SKETCH_DIRECTION_FILTERS)
        if was_sketch != self.sketch_mode:
            # model edges cannot drive sketch patterns and vice versa
            adsk.core.SelectionCommandInput.cast(self._input(ID_DIRECTION)).clearSelection()
            adsk.core.SelectionCommandInput.cast(self._input(ID_AXIS)).clearSelection()
        self._update_selection_inputs()
        sel.hasFocus = True

    def apply_preset(self, preset_id: str):
        preset = engine.preset_by_id(preset_id) or engine.preset_by_id('custom')
        self._show_notes(preset.id)
        if preset.id == 'custom':
            return
        adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA)).value = preset.formula
        self._fill_modes(self.circular, preset.mode)
        adsk.core.ValueCommandInput.cast(self._input(ID_A_LEN)).value = self.units.convert(preset.a_mm, 'mm', 'cm')
        adsk.core.ValueCommandInput.cast(self._input(ID_A_ANG)).value = math.radians(preset.a_deg)
        adsk.core.ValueCommandInput.cast(self._input(ID_B)).value = preset.b
        adsk.core.ValueCommandInput.cast(self._input(ID_C)).value = preset.c

    def _show_notes(self, preset_id: str):
        preset = engine.preset_by_id(preset_id) or engine.preset_by_id('custom')
        adsk.core.TextBoxCommandInput.cast(self._input(ID_NOTES)).formattedText = preset.notes

    def _switch_to_custom_if_edited(self):
        preset = engine.preset_by_id(self.preset_id)
        if preset is None or preset.id == 'custom':
            return
        if self.formula_text.strip() != preset.formula or self.mode != preset.mode:
            self._fill_presets(self.circular, 'custom')
            self._show_notes('custom')

    def refresh_preview(self):
        preview = adsk.core.TextBoxCommandInput.cast(self._input(ID_PREVIEW))
        formula = adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA))
        error = False
        try:
            spec = self.spec()
            result = engine.compute_positions(spec)
            html = engine.preview_html(result, self.length_unit, spec.circular, spec.mode, spec.flip)
            if self.live and spec.count > MAX_MODEL_PREVIEW:
                html += f'<br><i>Model preview is off above {MAX_MODEL_PREVIEW} copies; the result is built on OK.</i>'
            if self.last_error:
                html = engine.error_html(self.last_error) + '<br>' + html
                error = True
            formula.isValueError = False
        except engine.FormulaError as e:
            html = engine.error_html(str(e))
            formula.isValueError = True
            error = True
        except Exception as e:           # never let a preview problem take the dialog down
            html = engine.error_html(f'{type(e).__name__}: {e}')
            error = True
        preview.formattedText = html
        if error:
            try:
                self._input(ID_PREVIEW_GROUP).isExpanded = True   # the group is closed by default
            except Exception:
                pass
        try:
            self._update_edit_status()
        except Exception:
            pass

    def is_valid(self) -> bool:
        valid, why = self._validity()
        if self.editing is not None:
            _log(f'is_valid: {valid} ({why})')
        return valid

    def _validity(self):
        self._complete_pending()
        existing = adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING))
        if existing.selectionCount and self.editing is None:
            return False, 'something that is not a Formula Pattern copy is selected'
        if self.selected_objects().count < 1:
            return False, 'no objects'
        if self.reference_entity() is None and not (self.sketch_mode and not self.circular):
            return False, 'no direction / axis'
        try:
            engine.compute_positions(self.spec())
        except engine.FormulaError as e:
            return False, f'formula: {e}'
        return True, 'ok'

    # ---- building the result ----

    def build(self):
        """Create the pattern features. Raises on failure with a readable message."""
        self.last_error = None
        spec = self.spec()
        result = engine.compute_positions(spec)
        entities = self.selected_objects()
        reference = self.reference_entity()
        if entities.count < 1:
            raise RuntimeError('Select the objects to pattern first')
        if self.sketch_mode:
            if spec.circular and reference is None:
                raise RuntimeError('Select the centre point of the pattern first')
        elif reference is None:
            raise RuntimeError('Select the objects and a direction (or axis) first')
        # Every copy carries the pattern's id so the pattern can be found again from any copy.
        # An edited pattern keeps its id; a new one gets its own.
        self._pattern_id = self.editing.id if self.editing else uuid.uuid4().hex
        try:
            if self.editing:
                # The highlight must not keep hold of copies that are about to be deleted.
                adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING)).clearSelection()
                self._remove_existing(self.editing)
            if self.sketch_mode:
                return self._build_sketch(spec, result, entities, reference)
            return self._build_features(spec, result, entities, reference)
        except RuntimeError as e:
            if _FUSION_ERROR.match(str(e)):
                # Fusion's own "<code> : <what>" messages say nothing about the selection; add it.
                label = 'axis' if spec.circular else 'direction'
                raise RuntimeError(f'{e} — {_selection_summary(entities, reference, label)}') from e
            raise

    def _remove_existing(self, record):
        """Delete the copies of the pattern being edited.

        Features are deleted with the timeline rolled back to where they were, so the rebuilt
        ones (created at the marker) take the same place and anything after them stays after
        them; _build_features() puts the marker back afterwards. A sketch pattern constraint
        leaves its copied geometry behind when deleted, so the copies are deleted as well.
        """
        if record.kind == 'sketch':
            for constraint in record.copies:
                try:
                    copies = list(constraint.createdEntities)
                except Exception:
                    copies = []
                constraint.deleteMe()
                for e in copies:
                    try:
                        e.deleteMe()
                    except Exception:
                        pass
            return
        timeline = self.design.timeline
        self._marker_before = timeline.markerPosition
        self._marker_was_at_end = timeline.markerPosition >= timeline.count
        timeline.markerPosition = min(_timeline_index(f) for f in record.copies)
        for feature in record.copies:
            feature.deleteMe()

    def _restore_marker(self):
        timeline = self.design.timeline
        if self._marker_was_at_end:
            timeline.moveToEnd()
        else:
            timeline.markerPosition = min(self._marker_before, timeline.count)

    def _build_features(self, spec, result, entities, reference):
        component = self.design.activeComponent
        timeline = self.design.timeline
        start_index = timeline.markerPosition
        compute = self.compute_option
        group_name = self.group_name
        settings_json = json.dumps(self.collect_settings())
        n = result.count
        two = adsk.core.ValueInput.createByReal(2.0)

        created = []
        for k, position in enumerate(result.positions, 1):
            if spec.circular:
                features = component.features.circularPatternFeatures
                pattern_input = features.createInput(entities, reference)
                pattern_input.quantity = two
                pattern_input.totalAngle = adsk.core.ValueInput.createByString(f'{position:.9g} deg')
                pattern_input.isSymmetric = False
            else:
                features = component.features.rectangularPatternFeatures
                distance = adsk.core.ValueInput.createByString(f'{position:.9g} {self.length_unit}')
                pattern_input = features.createInput(
                    entities, reference, two, distance,
                    adsk.fusion.PatternDistanceType.ExtentPatternDistanceType)
            pattern_input.patternComputeOption = compute
            feature = features.add(pattern_input)
            if feature is None:
                where = f'{engine.format_number(position)}{"°" if spec.circular else " " + self.length_unit}'
                raise RuntimeError(f'Fusion could not create copy {k} of {n} at {where}')
            try:
                feature.name = f'{group_name} {k}/{n}'
                feature.attributes.add(ATTRIBUTE_GROUP, 'settings', settings_json)
                feature.attributes.add(ATTRIBUTE_GROUP, 'copy', str(k))
                feature.attributes.add(ATTRIBUTE_GROUP, 'pattern', self._pattern_id)
            except Exception:
                pass
            created.append(feature)

        end_index = timeline.markerPosition - 1
        if len(created) > 1 and end_index > start_index:
            try:
                group = timeline.timelineGroups.add(start_index, end_index)
                group.name = group_name
            except Exception:
                pass
        if self.editing:
            self._restore_marker()
        return created

    def _build_sketch(self, spec, result, entities, reference):
        """Sketch curves: one sketch pattern constraint (quantity 2) per copy, like the features."""
        # Geometry selected inside an occurrence arrives as a proxy; the sketch API wants the
        # sketch's own objects.
        items = [_native(e) for e in entities]
        reference = _native(reference) if reference is not None else None
        sketch = getattr(items[0], 'parentSketch', None)
        if sketch is None:
            raise RuntimeError('Select sketch curves or sketch points to pattern')
        if any(not _same_sketch(getattr(e, 'parentSketch', None), sketch) for e in items):
            raise RuntimeError('All selected curves must be in the same sketch')
        if reference is not None and not _same_sketch(getattr(reference, 'parentSketch', None), sketch):
            raise RuntimeError('The direction / centre must be in the same sketch as the curves')
        centre = None
        if spec.circular:
            centre = adsk.fusion.SketchPoint.cast(reference) or getattr(reference, 'centerSketchPoint', None)
            if centre is None:
                raise RuntimeError('The centre must be a sketch point or a circle')

        constraints = sketch.geometricConstraints
        settings_json = json.dumps(self.collect_settings())
        n = result.count
        two = adsk.core.ValueInput.createByReal(2.0)
        created = []
        for k, position in enumerate(result.positions, 1):
            if spec.circular:
                pattern_input = constraints.createCircularPatternInput(items, centre)
                if pattern_input:
                    pattern_input.quantity = two
                    pattern_input.totalAngle = adsk.core.ValueInput.createByString(f'{position:.9g} deg')
                    pattern_input.isSymmetric = False
                pattern = constraints.addCircularPattern(pattern_input) if pattern_input else None
            else:
                pattern_input = constraints.createRectangularPatternInput(
                    items, adsk.fusion.PatternDistanceType.ExtentPatternDistanceType)
                if pattern_input:
                    distance = adsk.core.ValueInput.createByString(f'{position:.9g} {self.length_unit}')
                    # The direction properties start out null, which Fusion treats as the sketch
                    # X axis (and, for direction two, perpendicular to it). Only set a direction
                    # when there is a line: setDirectionOne/Two(None, …) is rejected by Fusion.
                    if reference is not None:
                        pattern_input.setDirectionOne(reference, two, distance)
                    else:
                        pattern_input.quantityOne = two
                        pattern_input.distanceOne = distance
                    pattern_input.quantityTwo = adsk.core.ValueInput.createByReal(1.0)
                    pattern_input.distanceTwo = adsk.core.ValueInput.createByReal(1.0)   # unused with one row
                pattern = constraints.addRectangularPattern(pattern_input) if pattern_input else None
            if pattern is None:
                where = f'{engine.format_number(position)}{"°" if spec.circular else " " + self.length_unit}'
                raise RuntimeError(f'Fusion could not create copy {k} of {n} at {where}')
            try:
                pattern.attributes.add(ATTRIBUTE_GROUP, 'settings', settings_json)
                pattern.attributes.add(ATTRIBUTE_GROUP, 'copy', str(k))
                pattern.attributes.add(ATTRIBUTE_GROUP, 'pattern', self._pattern_id)
            except Exception:
                pass
            created.append(pattern)
        return created

    def show_build_error(self, message: str):
        self.last_error = message
        self.refresh_preview()

    # ---- remembering values ----

    def collect_settings(self) -> dict:
        return {
            'pattern_type': 'circular' if self.circular else 'linear',
            'object_type': self.object_type,
            'count': self.count,
            'preset': self.preset_id,
            'mode': self.mode,
            'formula': self.formula_text,
            'a_length': adsk.core.ValueCommandInput.cast(self._input(ID_A_LEN)).expression,
            'a_angle': adsk.core.ValueCommandInput.cast(self._input(ID_A_ANG)).expression,
            'b': adsk.core.ValueCommandInput.cast(self._input(ID_B)).expression,
            'c': adsk.core.ValueCommandInput.cast(self._input(ID_C)).expression,
            'flip': self.flip,
            'live': self.live,
            'compute': adsk.core.DropDownCommandInput.cast(self._input(ID_COMPUTE)).selectedItem.index,
            'group_name': self.group_name,
        }

    def _apply_settings(self, s: dict, force_object_type: bool = False):
        if not s:
            return

        def do(action):
            try:
                action()
            except Exception:
                pass

        def select(input_id, index):
            dd = adsk.core.DropDownCommandInput.cast(self._input(input_id))
            if 0 <= index < dd.listItems.count:
                dd.listItems.item(index).isSelected = True

        circular = s.get('pattern_type') == 'circular'
        if circular != self.circular:
            do(lambda: select(ID_TYPE, 1 if circular else 0))
            do(self.on_type_changed)
        names = [name for name, _ in OBJECT_TYPES]
        # While a sketch is being edited, sketch curves are what the user is after (unless the
        # settings come from a saved pattern, which knows what it patterned).
        if s.get('object_type') in names and (force_object_type or not self.active_sketch):
            do(lambda: select(ID_OBJECT_TYPE, names.index(s['object_type'])))
            do(self.on_object_type_changed)
        do(lambda: setattr(adsk.core.IntegerSpinnerCommandInput.cast(self._input(ID_COUNT)), 'value', int(s['count'])))
        do(lambda: self._fill_presets(circular, s.get('preset', 'custom')))
        do(lambda: self._show_notes(self.preset_id))
        do(lambda: self._fill_modes(circular, s.get('mode', engine.MODE_POSITION)))
        do(lambda: setattr(adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA)), 'value', str(s['formula'])))
        do(lambda: setattr(adsk.core.BoolValueCommandInput.cast(self._input(ID_FLIP)), 'value', bool(s['flip'])))
        do(lambda: setattr(adsk.core.BoolValueCommandInput.cast(self._input(ID_LIVE)), 'value', bool(s['live'])))
        do(lambda: select(ID_COMPUTE, int(s['compute'])))
        do(lambda: setattr(adsk.core.StringValueCommandInput.cast(self._input(ID_GROUP_NAME)), 'value', str(s['group_name'])))

    def _apply_number_settings(self, s: dict):
        """The saved text of a, b, c. Only possible once the dialog is on screen (see __init__)."""
        if not s:
            return
        for key, input_id in (('a_length', ID_A_LEN), ('a_angle', ID_A_ANG), ('b', ID_B), ('c', ID_C)):
            if s.get(key):
                try:
                    adsk.core.ValueCommandInput.cast(self._input(input_id)).expression = str(s[key])
                except Exception:
                    pass


_FUSION_ERROR = re.compile(r'^\d+ : ')   # how Fusion's API phrases its own errors


# ---------------------------------------------------------------------------
# Finding a saved pattern again
# ---------------------------------------------------------------------------

class NotAPattern(ValueError):
    """The selected entity is not a copy made by Formula Pattern; the message says why."""


class PatternRecord:
    """A saved pattern, found from its copies.

    ``copies`` are the pattern features (kind 'features') or sketch pattern constraints
    (kind 'sketch') that make it up; ``objects`` and ``reference`` are what it patterned
    and along / around what; ``highlight`` is what to show the user (the features, or the
    copied sketch curves); ``position`` orders patterns the way the timeline does.
    """

    def __init__(self, pattern_id, settings, copies, kind, objects, reference, label, highlight, position):
        self.id = pattern_id
        self.settings = settings
        self.copies = copies
        self.kind = kind
        self.objects = objects
        self.reference = reference
        self.label = label
        self.highlight = highlight
        self.position = position


def find_pattern(design, entity, known=()) -> PatternRecord:
    """The saved pattern that ``entity`` (a feature, a constraint or a patterned sketch
    curve) is a copy of. ``known`` are the records already listed, searched first for sketch
    curves. Raises NotAPattern otherwise."""
    native = _native(entity)
    source = _pattern_source(native)
    if source is None:
        record = _pattern_containing(design, native, known)
        if record is not None:
            return record
        raise NotAPattern('This is not a copy made by Formula Pattern')
    pattern_id = _attribute(source, 'pattern')
    if not pattern_id:
        raise NotAPattern('This copy was made by an older version of Formula Pattern and cannot be '
                          'loaded; delete its group and create the pattern again')
    copies = [a.parent for a in _find_attributes(design) if a.value == pattern_id and a.parent is not None]
    copies += [e for e in _timeline_entities(design) if _attribute(e, 'pattern') == pattern_id]
    return _record(pattern_id, _distinct(copies) or [source])


def list_patterns(design, sketch=None) -> list:
    """Every saved pattern in the design, in timeline order (plus the sketch patterns of
    ``sketch``, the sketch being edited). Finds them through Design.findAttributes and, in
    case that misses some, by walking the timeline and the sketch's constraints."""
    copies = {}                                   # pattern id → its copies
    for a in _find_attributes(design):
        if a.value and a.parent is not None:
            copies.setdefault(a.value, []).append(a.parent)
    candidates = list(_timeline_entities(design))
    if sketch is not None:
        try:
            candidates += list(sketch.geometricConstraints)
        except Exception:
            pass
    for entity in candidates:
        pattern_id = _attribute(entity, 'pattern')
        if pattern_id:
            copies.setdefault(pattern_id, []).append(entity)
    records = [_record(pattern_id, _distinct(items)) for pattern_id, items in copies.items()]
    records.sort(key=lambda r: (r.position, r.label))
    seen = set()
    for record in records:                        # the drop-down needs distinct labels
        base, k = record.label, 2
        while record.label in seen:
            record.label = f'{base} #{k}'
            k += 1
        seen.add(record.label)
    return records


def _record(pattern_id, copies) -> PatternRecord:
    copies = sorted(copies, key=_copy_number)
    source = copies[0]
    try:
        settings = json.loads(_attribute(source, 'settings') or '{}')
    except ValueError:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    kind = 'sketch' if _type_name(source).endswith('Constraint') else 'features'
    objects, reference = [], None          # filled by read_inputs() when the pattern is chosen
    n = len(copies)
    count = f'{n} {"copy" if n == 1 else "copies"}'
    position = 10 ** 9            # sketch patterns and anything not in the timeline go last
    if kind == 'sketch':
        highlight = []
        for constraint in copies:
            try:
                highlight.extend(list(constraint.createdEntities))
            except Exception:
                pass
        try:
            where = source.parentSketch.name
        except Exception:
            where = 'Sketch'
        label = f'{where} sketch pattern ({count})'
    else:
        highlight = list(copies)
        try:
            position = min(_timeline_index(c) for c in copies)
        except Exception:
            pass
        label = f'{settings.get("group_name") or DEFAULT_GROUP_NAME} ({count})'
    return PatternRecord(pattern_id, settings, copies, kind, objects, reference, label, highlight, position)


def read_inputs(design, record):
    """Fill ``record.objects`` and ``record.reference`` from its first copy.

    A pattern feature gives its inputEntities and direction back only while the timeline
    marker sits just before it (API reference; read elsewhere they came back empty, Fusion
    2026-11-06), so the marker is rolled there for the read and put back afterwards. Sketch
    constraints can be read as they are.
    """
    source = record.copies[0]
    circular = _type_name(source).startswith('Circular')
    record.objects, record.reference = [], None
    timeline = design.timeline
    before = timeline.markerPosition
    rolled = False
    try:
        if record.kind == 'sketch':
            record.objects = list(source.entities)
            record.reference = source.centerPoint if circular else source.directionOneEntity
        else:
            timeline.markerPosition = _timeline_index(source)
            rolled = True
            inputs = source.inputEntities
            record.objects = [inputs.item(k) for k in range(inputs.count)]
            record.reference = source.axis if circular else source.directionOneEntity
        _log(f'  read_inputs of {_describe(source)}: objects={[_describe(e) for e in record.objects]} '
             f'reference={_describe(record.reference) if record.reference is not None else None}')
    except Exception:
        _log(f'  read_inputs of {_describe(source)} failed:\n{traceback.format_exc()}')
    finally:
        if rolled:
            try:
                timeline.markerPosition = before
            except Exception:
                timeline.moveToEnd()


def _find_attributes(design) -> list:
    try:
        return list(design.findAttributes(ATTRIBUTE_GROUP, 'pattern'))
    except Exception:
        return []


def _timeline_entities(design) -> list:
    """Every feature in the timeline, inside collapsed groups too."""
    found = []
    try:
        timeline = design.timeline
        for i in range(timeline.count):
            item = timeline.item(i)
            group = adsk.fusion.TimelineGroup.cast(item)
            if group is not None:
                found += [group.item(j).entity for j in range(group.count)]
            else:
                found.append(item.entity)
    except Exception:
        pass
    return [e for e in found if e is not None]


def _distinct(entities) -> list:
    """Drop duplicates (Fusion hands out a fresh wrapper object for every lookup)."""
    seen, out = set(), []
    for e in entities:
        try:
            key = e.entityToken or id(e)
        except Exception:
            key = id(e)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _pattern_source(entity):
    """The pattern feature / constraint ``entity`` is or belongs to, else None."""
    if _attribute(entity, 'copy') is not None:
        return entity
    try:
        constraints = list(entity.geometricConstraints)   # a sketch curve or point
    except Exception:
        return None
    _log(f'  constraints of {_describe(entity)}: {[_type_name(c) for c in constraints]}')
    for constraint in constraints:
        if _attribute(constraint, 'copy') is not None:
            return constraint
    return None


def _pattern_containing(design, entity, known=()):
    """The pattern whose copies, or patterned originals, include ``entity``. This is how a
    clicked sketch curve is recognised: Fusion does not list the pattern constraint among
    the curve's own geometricConstraints (seen 2026-11-06)."""
    token = _token(entity)
    if token is None:
        return None
    records = list(known) or list_patterns(design, getattr(entity, 'parentSketch', None))
    for record in records:
        members = list(record.highlight)
        if record.kind == 'sketch':
            for copy in record.copies:
                try:
                    members += list(copy.entities)
                except Exception:
                    pass
        if any(_token(m) == token for m in members):
            return record
    return None


def _token(entity):
    try:
        return entity.entityToken or None
    except Exception:
        return None


def _attribute(entity, name):
    try:
        attribute = entity.attributes.itemByName(ATTRIBUTE_GROUP, name)
    except Exception:
        return None
    return attribute.value if attribute is not None else None


def _copy_number(entity) -> int:
    try:
        return int(_attribute(entity, 'copy') or 0)
    except ValueError:
        return 0


def _timeline_index(feature) -> int:
    """Where a feature sits in the timeline. Inside a collapsed group the feature's own index
    is not available (the reference says -1; Fusion 2026-11 raises "InternalValidationError :
    res >= 0") and the group's index is the position."""
    tl = feature.timelineObject
    candidates = [tl]
    try:
        group = tl.parentGroup
    except Exception:
        group = None
    if group is not None:
        candidates.append(group)
    for item in candidates:
        try:
            index = item.index
        except Exception:
            continue
        if index is not None and index >= 0:
            return index
    raise RuntimeError('Formula Pattern could not find the saved pattern in the timeline')


def _native(entity):
    """The object in its own component when ``entity`` is an occurrence proxy, else ``entity``."""
    try:
        native = entity.nativeObject
    except Exception:
        return entity
    return native if native is not None else entity


def _type_name(entity) -> str:
    try:
        return entity.objectType.split('::')[-1]
    except Exception:
        return type(entity).__name__


def _describe(entity) -> str:
    """'SketchLine 'centre line' in sketch 'Fretboard' via Neck:1' — for error messages."""
    text = _type_name(entity)
    for attr, fmt in (('name', " '{}'",), ('parentSketch', " in sketch '{}'"), ('assemblyContext', ' via {}')):
        try:
            value = getattr(entity, attr, None)
            if value:
                text += fmt.format(value if attr == 'name' else value.name)
        except Exception:
            pass
    return text


def _selection_summary(entities, reference, label: str) -> str:
    items = list(entities)
    parts = []
    if reference is not None:
        parts.append(f'{label}: {_describe(reference)}')
    parts.append(f'objects: {len(items)} × {_type_name(items[0])}' if items else 'objects: none')
    return '; '.join(parts)


def _same_entity(a, b) -> bool:
    """Fusion hands out a fresh wrapper object for every read, so compare by token."""
    if a is None or b is None:
        return False
    if a is b:
        return True
    try:
        return a.entityToken == b.entityToken
    except Exception:
        try:
            return a.name == b.name
        except Exception:
            return False


_same_sketch = _same_entity


def _in_root_context(design, entity):
    """``entity`` as a selection input wants it: the proxy in the root component's context when
    it lives in another component, which is what Fusion hands out when the user clicks it. A
    pattern feature reads its inputs back in its own component's context instead."""
    try:
        if getattr(entity, 'assemblyContext', None) is not None:
            return entity                       # already a proxy
        component = _owning_component(entity)
        root = design.rootComponent
        if component is None or _same_entity(component, root):
            return entity
        occurrences = root.allOccurrencesByComponent(component)
        if occurrences.count == 0:
            return entity
        return entity.createForAssemblyContext(occurrences.item(0)) or entity
    except Exception:
        _log(f'  _in_root_context({_describe(entity)}) failed:\n{traceback.format_exc()}')
        return entity


def _owning_component(entity):
    """The component an entity lives in: bodies, features and sketches say so themselves; a
    face, edge or vertex only knows its body; a sketch entity knows its sketch."""
    for path in (('parentComponent',), ('body', 'parentComponent'), ('parentSketch', 'parentComponent')):
        value = entity
        try:
            for name in path:
                value = getattr(value, name)
        except Exception:
            continue
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------

class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args: adsk.core.CommandCreatedEventArgs):
        try:
            cmd = args.command
            cmd.setDialogMinimumSize(420, 520)
            cmd.setDialogInitialSize(460, 760)
            cmd.okButtonText = 'OK'
            dialog = Dialog(cmd)
            for event, handler_class in (
                (cmd.activate, ActivateHandler),
                (cmd.inputChanged, InputChangedHandler),
                (cmd.validateInputs, ValidateInputsHandler),
                (cmd.executePreview, ExecutePreviewHandler),
                (cmd.execute, ExecuteHandler),
                (cmd.destroy, DestroyHandler),
            ):
                handler = handler_class(dialog)
                event.add(handler)
                _handlers.append(handler)
        except Exception:
            _report('could not open the dialog')


class ActivateHandler(adsk.core.CommandEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.CommandEventArgs):
        try:
            self.dialog.on_activate()
        except Exception:
            _report('failed while preparing the dialog')


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.InputChangedEventArgs):
        try:
            self.dialog.on_input_changed(args.input)
        except Exception:
            _report('failed while updating the dialog')


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.ValidateInputsEventArgs):
        try:
            args.areInputsValid = self.dialog.is_valid()
        except Exception:
            args.areInputsValid = False


class ExecutePreviewHandler(adsk.core.CommandEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.CommandEventArgs):
        dialog = self.dialog
        try:
            if dialog.unchanged or not dialog.live or dialog.count > MAX_MODEL_PREVIEW or not dialog.is_valid():
                return
            had_error = dialog.last_error is not None
            dialog.build()
            args.isValidResult = True     # OK keeps this result; execute is not called again
            if had_error:
                dialog.refresh_preview()  # clear the earlier build error from the preview box
        except Exception as e:
            args.isValidResult = False
            try:
                dialog.show_build_error(str(e) or 'Fusion could not build the pattern')
            except Exception:
                pass


class ExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.CommandEventArgs):
        try:
            if self.dialog.unchanged:
                return                # OK on a loaded pattern with nothing changed: leave it
            self.dialog.build()
        except Exception as e:
            args.executeFailed = True
            args.executeFailedMessage = f'Formula Pattern: {e}'


class DestroyHandler(adsk.core.CommandEventHandler):
    def __init__(self, dialog: Dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args: adsk.core.CommandEventArgs):
        try:
            if args.terminationReason == adsk.core.CommandTerminationReason.CompletedTerminationReason:
                save_settings(self.dialog.collect_settings())
        except Exception:
            pass
        finally:
            # Drop this dialog's handlers; the command-created handler stays for the next run.
            _handlers[:] = [h for h in _handlers if getattr(h, 'dialog', None) is not self.dialog]
