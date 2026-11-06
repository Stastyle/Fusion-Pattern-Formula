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
ID_EXISTING = 'fpExisting'

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
    'existing': 'Click any copy of a saved Formula Pattern to load and change it',
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
    if _ui:
        _ui.messageBox(f'Formula Pattern: {what}\n\n{traceback.format_exc()}')


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------

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
        group.isExpanded = True
        preview = group.children.addTextBoxCommandInput(ID_PREVIEW, '', '', 9, True)
        preview.isFullWidth = True
        live = group.children.addBoolValueInput(ID_LIVE, 'Show in model', True, '', True)
        live.tooltip = f'Build the copies while the dialog is open. Turned off automatically above {MAX_MODEL_PREVIEW} copies.'

        group = inputs.addGroupCommandInput(ID_EDIT_GROUP, 'Edit a saved pattern')
        group.isExpanded = True
        sel = group.children.addSelectionInput(ID_EXISTING, 'Saved pattern', PROMPTS['existing'])
        self._set_filters(sel, EXISTING_FILTERS)
        sel.setSelectionLimits(0, 1)
        sel.tooltip = ('Click any copy of a pattern made by Formula Pattern: a feature in its timeline group '
                       '(or a face it created), or a patterned sketch curve. The dialog loads that pattern\'s '
                       'formula, values, objects and direction, and OK replaces it in place. '
                       'Clear this to make a new pattern instead.')

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
        if cid == ID_TYPE:
            self.on_type_changed()
        elif cid == ID_OBJECT_TYPE:
            self.on_object_type_changed()
            return
        elif cid == ID_PRESET:
            self.apply_preset(self.preset_id)
        elif cid == ID_FORMULA:
            self._switch_to_custom_if_edited()
        elif cid == ID_MODE:
            self._switch_to_custom_if_edited()
        elif cid == ID_EXISTING:
            self.on_existing_changed()
        elif cid not in (ID_COUNT, ID_A_LEN, ID_A_ANG, ID_B, ID_C, ID_FLIP, ID_LIVE,
                         ID_OBJECTS, ID_DIRECTION, ID_AXIS):
            return
        self.refresh_preview()

    def on_existing_changed(self):
        """The "Saved pattern" selection: load the pattern it belongs to, or say why not."""
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING))
        self.last_error = None
        self.editing = None
        if sel.selectionCount == 0:
            return
        try:
            record = find_pattern(self.design, sel.selection(0).entity)
        except NotAPattern as e:
            self.last_error = str(e)
            return
        self.load_pattern(record)

    def load_pattern(self, record):
        """Fill the dialog from a saved pattern; OK will then replace it (see build())."""
        self._apply_settings(record.settings, force_object_type=True)
        self._apply_number_settings(record.settings)
        self._fill_selection(ID_OBJECTS, record.objects)
        self._fill_selection(ID_AXIS if self.circular else ID_DIRECTION,
                             [record.reference] if record.reference is not None else [])
        self.editing = record

    def _fill_selection(self, input_id, entities):
        sel = adsk.core.SelectionCommandInput.cast(self._input(input_id))
        sel.clearSelection()
        for e in entities:
            try:
                sel.addSelection(e)
            except Exception:
                pass              # the user re-selects what Fusion would not take back

    def _editing_note(self) -> str:
        record = self.editing
        n = len(record.copies)
        what = 'sketch pattern' if record.kind == 'sketch' else \
            f'“{record.settings.get("group_name") or DEFAULT_GROUP_NAME}”'
        return f'✎ Editing <b>{what}</b> ({n} {"copy" if n == 1 else "copies"}): OK replaces it.'

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
        try:
            spec = self.spec()
            result = engine.compute_positions(spec)
            html = engine.preview_html(result, self.length_unit, spec.circular, spec.mode, spec.flip)
            if self.live and spec.count > MAX_MODEL_PREVIEW:
                html += f'<br><i>Model preview is off above {MAX_MODEL_PREVIEW} copies; the result is built on OK.</i>'
            if self.editing:
                html = self._editing_note() + '<br>' + html
            if self.last_error:
                html = engine.error_html(self.last_error) + '<br>' + html
            formula.isValueError = False
        except engine.FormulaError as e:
            html = engine.error_html(str(e))
            formula.isValueError = True
        except Exception as e:           # never let a preview problem take the dialog down
            html = engine.error_html(f'{type(e).__name__}: {e}')
        preview.formattedText = html

    def is_valid(self) -> bool:
        existing = adsk.core.SelectionCommandInput.cast(self._input(ID_EXISTING))
        if existing.selectionCount and self.editing is None:
            return False          # something that is not a Formula Pattern copy is selected
        if self.selected_objects().count < 1:
            return False
        if self.reference_entity() is None and not (self.sketch_mode and not self.circular):
            return False
        try:
            engine.compute_positions(self.spec())
        except engine.FormulaError:
            return False
        return True

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
    """A saved pattern, found from one of its copies.

    ``copies`` are the pattern features (kind 'features') or sketch pattern constraints
    (kind 'sketch') that make it up; ``objects`` and ``reference`` are what it patterned
    and along / around what.
    """

    def __init__(self, pattern_id, settings, copies, kind, objects, reference):
        self.id = pattern_id
        self.settings = settings
        self.copies = copies
        self.kind = kind
        self.objects = objects
        self.reference = reference


def find_pattern(design, entity) -> PatternRecord:
    """The saved pattern that ``entity`` (a feature, a constraint or a patterned sketch
    curve) is a copy of. Raises NotAPattern otherwise."""
    source = _pattern_source(_native(entity))
    if source is None:
        raise NotAPattern('This is not a copy made by Formula Pattern')
    pattern_id = _attribute(source, 'pattern')
    if not pattern_id:
        raise NotAPattern('This copy was made by an older version of Formula Pattern and cannot be '
                          'loaded; delete its group and create the pattern again')
    try:
        settings = json.loads(_attribute(source, 'settings') or '{}')
    except ValueError:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    copies = [a.parent for a in design.findAttributes(ATTRIBUTE_GROUP, 'pattern')
              if a.value == pattern_id and a.parent is not None]
    if not copies:
        copies = [source]
    copies.sort(key=_copy_number)
    kind = 'sketch' if _type_name(source).endswith('Constraint') else 'features'
    circular = _type_name(source).startswith('Circular')
    first = copies[0]
    objects, reference = [], None
    try:
        if kind == 'sketch':
            objects = list(first.entities)
            reference = first.centerPoint if circular else first.directionOneEntity
        else:
            inputs = first.inputEntities
            objects = [inputs.item(k) for k in range(inputs.count)]
            reference = first.axis if circular else first.directionOneEntity
    except Exception:
        pass                      # the user re-selects what could not be read back
    return PatternRecord(pattern_id, settings, copies, kind, objects, reference)


def _pattern_source(entity):
    """The pattern feature / constraint ``entity`` is or belongs to, else None."""
    if _attribute(entity, 'copy') is not None:
        return entity
    try:
        constraints = list(entity.geometricConstraints)   # a sketch curve or point
    except Exception:
        return None
    for constraint in constraints:
        if _attribute(constraint, 'copy') is not None:
            return constraint
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
    """Where a feature sits in the timeline. A feature inside a collapsed group reports -1;
    the group's own index is the position then."""
    tl = feature.timelineObject
    index = tl.index
    if index < 0:
        group = tl.parentGroup
        if group is not None:
            index = group.index
    if index < 0:
        raise RuntimeError('Formula Pattern could not find the saved pattern in the timeline')
    return index


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


def _same_sketch(a, b) -> bool:
    """Fusion hands out a fresh proxy object for every parentSketch read, so compare by token."""
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
            if not dialog.live or dialog.count > MAX_MODEL_PREVIEW or not dialog.is_valid():
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
