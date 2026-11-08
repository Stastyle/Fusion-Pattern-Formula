"""Fusion 360 side of the Formula Pattern add-in: toolbar button, dialog, feature creation.

Everything that does not need Fusion (formula evaluation, presets, preview text)
lives in engine.py so it can be tested outside Fusion; the vector and matrix helpers
that read Fusion entities live in placement.py.

How the result is built — two engines
-------------------------------------
*Native patterns* (``_build_features`` / ``_build_sketch``): for a Linear or Circular
pattern where nothing varies, every copy k is one *native* Rectangular (or Circular)
Pattern feature — or sketch pattern constraint — with quantity 2 and extent (or total
angle) equal to the formula's value for that copy. That works for faces, bodies,
features and components alike, keeps cuts behaving as cuts, and leaves every distance
as an ordinary editable model parameter.

*Transform engine* (``_build_transformed``): for Path patterns, for a Rotation or Size
formula, and for Profiles, every copy is placed by an explicit rigid transform
M_i = P_i · R_i (R_i the copy's own rotation, P_i the pattern placement). Bodies are
copied (Copy/Paste), optionally scaled, then moved by a free Move; components become
new occurrences; profiles are extruded by their own depth, moved and optionally
joined to / cut from a target body; sketch curves are copied with ``Sketch.copy``.

Both engines name every feature ``<group> k/n``, tag it with attributes (so a saved
pattern can be found again from any copy) and collect everything into one timeline
group.
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
from . import placement
from .settings import load_settings, save_settings

# ---------------------------------------------------------------------------
# Identifiers and constants
# ---------------------------------------------------------------------------

CMD_ID = 'stasm_FormulaPattern'
CMD_NAME = 'Formula Pattern'
CMD_TOOLTIP = ('Pattern objects along a direction, around an axis or along a two-formula path, where '
               'formulas decide the position of every copy — and, if you like, its rotation, size or '
               'extrusion depth: guitar frets, growing gaps, spirals, sunflower layouts, twisting '
               'louvres, drill indexes, QRD diffusers … Pick a preset or write your own formula.')

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
# 1.2: Path patterns and "Vary each copy"
ID_DIRECTION2 = 'fpDirection2'       # Path, solid: second direction (Y)
ID_COORDS = 'fpCoords'               # Path: 'X / Y' | 'Radius / Angle'
ID_FORMULA2 = 'fpFormula2'           # Path: the Y (or angle) formula
ID_VARY_GROUP = 'fpVaryGroup'        # group "Vary each copy", collapsed
ID_ROTATION = 'fpRotation'           # degrees formula, '' = none
ID_ROTATION_AXIS = 'fpRotationAxis'  # solid: an axis (DIRECTION_FILTERS); sketch: a centre point
ID_SIZE = 'fpSize'                   # size formula, '' = none
ID_SIZE_KIND = 'fpSizeKind'          # 'Scale factor' | 'Diameter (circles)'
ID_SCALE_POINT = 'fpScalePoint'      # solid: point to scale about
ID_DEPTH = 'fpDepth'                 # Profiles: depth formula, document units
ID_OPERATION = 'fpOperation'         # Profiles: 'New body' | 'Join' | 'Cut'
ID_TARGET = 'fpTarget'               # Profiles, Join / Cut: the target body

PATTERN_TYPES = ('Linear', 'Circular', 'Path')
PATTERN_KINDS = ('linear', 'circular', 'path')       # same order; the engine's names
OBJECT_SKETCH = 'Sketch curves'
OBJECT_PROFILES = 'Profiles'
OBJECT_TYPES = (
    ('Bodies', ('SolidBodies', 'SurfaceBodies')),
    ('Features', ('Features',)),
    ('Faces', ('Faces',)),
    ('Components', ('Occurrences',)),
    (OBJECT_SKETCH, ('SketchCurves', 'SketchPoints')),
    (OBJECT_PROFILES, ('Profiles',)),
)
# Object types a native pattern feature can handle but the transform engine cannot.
NATIVE_ONLY_TYPES = ('Features', 'Faces')
COORDS_OPTIONS = ('X / Y', 'Radius / Angle')
SIZE_KINDS = (('Scale factor', engine.SIZE_SCALE), ('Diameter (circles)', engine.SIZE_DIAMETER))
OPERATIONS = (
    ('New body', adsk.fusion.FeatureOperations.NewBodyFeatureOperation),
    ('Join', adsk.fusion.FeatureOperations.JoinFeatureOperation),
    ('Cut', adsk.fusion.FeatureOperations.CutFeatureOperation),
)
DIRECTION_FILTERS = ('LinearEdges', 'ConstructionLines', 'SketchLines')
AXIS_FILTERS = ('LinearEdges', 'ConstructionLines', 'SketchLines', 'CircularEdges', 'SketchCircles',
                'CylindricalFaces', 'ConicalFaces', 'ToroidalFaces')
# Sketch patterns are driven by sketch geometry only: a line for the direction (or none for
# the sketch X axis) and a point / circle centre for the centre of a circular pattern.
SKETCH_DIRECTION_FILTERS = ('SketchLines',)
SKETCH_AXIS_FILTERS = ('SketchPoints', 'SketchCircles')
ROTATION_CENTRE_FILTERS = ('SketchPoints',)
SCALE_POINT_FILTERS = ('Vertices', 'SketchPoints', 'ConstructionPoints')
TARGET_FILTERS = ('SolidBodies',)
# A saved pattern is picked through any of its copies: a pattern feature (clicked in the
# timeline, or through a face it made), a patterned sketch curve / point, or a body or
# occurrence the transform engine made.
EXISTING_FILTERS = ('Features', 'SketchCurves', 'SketchPoints', 'Bodies', 'Occurrences')
PROMPTS = {
    'objects': 'Select the objects to pattern',
    'objects_sketch': 'Select the sketch curves or points to pattern',
    'direction': 'Select a linear edge, sketch line or construction axis',
    'direction_sketch': 'Select a sketch line, or leave empty to use the sketch X axis',
    'direction_path': 'Select the X direction of the path: a linear edge, sketch line or construction axis',
    'direction_path_sketch': 'Select a sketch line for the X direction of the path, or leave empty to use the sketch X axis',
    'direction2': 'Select the Y direction of the path: a linear edge, sketch line or construction axis',
    'axis': 'Select an axis: edge, circle, cylindrical face, sketch line or construction axis',
    'axis_sketch': 'Select the centre: a sketch point or a circle',
    'rotation_axis': 'Select the axis every copy turns about: a linear edge, sketch line or construction axis',
    'rotation_centre': 'Select the sketch point every copy turns about, or leave empty to turn about the centre of the selection',
    'scale_point': 'Select the point every copy is scaled about: a vertex, sketch point or construction point',
    'target': 'Select the body the copies are joined to or cut from',
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
                       'face it created, a copied body or component, or a patterned sketch curve) picks '
                       'that pattern instead.')
        status = group.children.addTextBoxCommandInput(ID_EDIT_STATUS, '', '', 2, True)
        status.isFullWidth = True
        status.isVisible = False

        dd = inputs.addDropDownCommandInput(ID_TYPE, 'Pattern type', adsk.core.DropDownStyles.TextListDropDownStyle)
        for k, name in enumerate(PATTERN_TYPES):
            dd.listItems.add(name, k == 0)
        dd.tooltip = ('Linear: copies along a direction. Circular: copies around an axis. '
                      'Path: two formulas give the X / Y (or radius / angle) position of every copy in a plane.')

        default_type = OBJECT_SKETCH if self.active_sketch else 'Bodies'
        dd = inputs.addDropDownCommandInput(ID_OBJECT_TYPE, 'Object type', adsk.core.DropDownStyles.TextListDropDownStyle)
        for name, _ in OBJECT_TYPES:
            dd.listItems.add(name, name == default_type)
        dd.tooltip = ('What kind of objects to pattern (all selected objects must be of this kind). '
                      '"Sketch curves" patterns inside a sketch, like the native sketch patterns. '
                      '"Profiles" extrudes a sketch profile by its own Depth for every copy.')

        sel = inputs.addSelectionInput(ID_OBJECTS, 'Objects', PROMPTS['objects'])
        sel.setSelectionLimits(1, 0)

        sel = inputs.addSelectionInput(ID_DIRECTION, 'Direction', PROMPTS['direction'])
        sel.tooltip = 'Copies move along this. Use "Flip direction" if they go the wrong way.'

        # Input labels cannot change once created (CommandInput.name is read-only), so the
        # Path inputs carry fixed names; prompts, tooltips and the notes say what each means.
        sel = inputs.addSelectionInput(ID_DIRECTION2, 'Direction 2 (Y)', PROMPTS['direction2'])
        sel.isVisible = False
        sel.setSelectionLimits(0, 1)
        sel.tooltip = ('Path patterns: the Y direction of the path, made perpendicular to Direction (the X '
                       'direction). In a sketch the perpendicular in the sketch plane is used instead.')

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
        self._fill_presets('linear', select_id='custom')

        dd = g.addDropDownCommandInput(ID_COORDS, 'Coordinates', adsk.core.DropDownStyles.TextListDropDownStyle)
        for k, name in enumerate(COORDS_OPTIONS):
            dd.listItems.add(name, k == 0)
        dd.isVisible = False
        dd.tooltip = ('Path patterns: what the two formulas give for copy i — its X and Y offset from the '
                      f'original (in {self.length_unit}), or its radius (in {self.length_unit}) and angle (degrees).')

        dd = g.addDropDownCommandInput(ID_MODE, 'Formula gives', adsk.core.DropDownStyles.TextListDropDownStyle)
        dd.tooltip = ('"Distance from original": the formula is where copy i sits, measured from the original. '
                      '"Gap to previous": the formula is the step from copy i-1 to copy i; positions add up.')
        self._fill_modes(circular=False, mode=engine.MODE_POSITION)

        formula = g.addStringValueInput(ID_FORMULA, 'Formula', 'a * i')
        formula.tooltip = 'Math in i (copy number), n (copies), t = i/n, a, b, c and any user parameter. ^ is power. See Formula reference below.'

        formula2 = g.addStringValueInput(ID_FORMULA2, 'Second formula', '0')
        formula2.isVisible = False
        formula2.tooltip = 'Path patterns: the Y offset (or the angle in degrees) of copy i; same variables as the formula above.'

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

        unit = self.length_unit
        group = inputs.addGroupCommandInput(ID_VARY_GROUP, 'Vary each copy')
        group.isExpanded = False
        g = group.children
        rotation = g.addStringValueInput(ID_ROTATION, 'Rotation', '')
        rotation.tooltip = ('Formula for the angle in degrees every copy is turned by about the rotation axis '
                            '(or centre) before it is moved into place; empty = no rotation.')
        sel = g.addSelectionInput(ID_ROTATION_AXIS, 'Rotation axis', PROMPTS['rotation_axis'])
        sel.setSelectionLimits(0, 1)
        sel.tooltip = ('The axis every copy turns about (where the original sits); in a sketch, the sketch point '
                       'to turn about, or empty for the centre of the selected curves.')
        size = g.addStringValueInput(ID_SIZE, 'Size', '')
        size.tooltip = (f'Formula for the size of every copy: a scale factor (1 = unchanged) or, for sketch circles, '
                        f'the diameter in {unit}; empty = no change.')
        dd = g.addDropDownCommandInput(ID_SIZE_KIND, 'Size gives', adsk.core.DropDownStyles.TextListDropDownStyle)
        for k, (name, _) in enumerate(SIZE_KINDS):
            dd.listItems.add(name, k == 0)
        dd.tooltip = (f'"Scale factor": the Size formula multiplies the copy (bodies, profiles, sketch curves). '
                      f'"Diameter (circles)": it is the diameter of every copied sketch circle in {unit}.')
        sel = g.addSelectionInput(ID_SCALE_POINT, 'Scale about', PROMPTS['scale_point'])
        sel.setSelectionLimits(0, 1)
        sel.tooltip = 'The point every copy is scaled about before it is moved into place (bodies and profiles).'
        depth = g.addStringValueInput(ID_DEPTH, 'Depth', '')
        depth.tooltip = (f'Profiles: formula for the extrusion depth of every copy in {unit}; a negative depth '
                         'extrudes the other way, 0 is not allowed.')
        dd = g.addDropDownCommandInput(ID_OPERATION, 'Operation', adsk.core.DropDownStyles.TextListDropDownStyle)
        for k, (name, _) in enumerate(OPERATIONS):
            dd.listItems.add(name, k == 0)
        dd.tooltip = ('Profiles: keep every copy as a new body, or join all copies to / cut all copies from '
                      'the target body in one Combine.')
        sel = g.addSelectionInput(ID_TARGET, 'Target body', PROMPTS['target'])
        sel.setSelectionLimits(0, 1)
        sel.tooltip = 'The solid body the extruded copies are joined to or cut from.'

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
        """Visibility, filters, limits and prompts of every input that depends on the pattern
        type, the object type, the coordinates, the size kind or the operation."""
        kind, sketch = self.pattern_type, self.sketch_mode
        circular, path = kind == 'circular', kind == 'path'
        profiles, polar = self.profiles_mode, self.polar
        objects = adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS))
        direction = adsk.core.SelectionCommandInput.cast(self._input(ID_DIRECTION))
        direction2 = adsk.core.SelectionCommandInput.cast(self._input(ID_DIRECTION2))
        axis = adsk.core.SelectionCommandInput.cast(self._input(ID_AXIS))
        rotation_axis = adsk.core.SelectionCommandInput.cast(self._input(ID_ROTATION_AXIS))
        scale_point = adsk.core.SelectionCommandInput.cast(self._input(ID_SCALE_POINT))
        target = adsk.core.SelectionCommandInput.cast(self._input(ID_TARGET))

        self._set_filters(objects, dict(OBJECT_TYPES)[self.object_type])
        objects.commandPrompt = PROMPTS['objects_sketch' if sketch else 'objects']

        direction.isVisible = not circular
        direction2.isVisible = path and not sketch      # a sketch uses its in-plane perpendicular
        axis.isVisible = circular
        # A hidden input must not demand a selection; a sketch direction is optional (X axis).
        direction.setSelectionLimits(0 if (circular or sketch) else 1, 1)
        direction2.setSelectionLimits(1 if direction2.isVisible else 0, 1)
        axis.setSelectionLimits(1 if circular else 0, 1)
        self._set_filters(direction, SKETCH_DIRECTION_FILTERS if sketch else DIRECTION_FILTERS)
        self._set_filters(direction2, DIRECTION_FILTERS)
        self._set_filters(axis, SKETCH_AXIS_FILTERS if sketch else AXIS_FILTERS)
        direction.commandPrompt = PROMPTS[('direction_path' if path else 'direction') + ('_sketch' if sketch else '')]
        direction.tooltip = ('Path: copies are placed along this (X) and Direction 2 (Y). '
                             if path else 'Copies move along this. ') + 'Use "Flip direction" to mirror them.'
        axis.commandPrompt = PROMPTS['axis_sketch' if sketch else 'axis']

        # Path: two formulas and a coordinate choice instead of "Formula gives".
        self._input(ID_COORDS).isVisible = path
        self._input(ID_MODE).isVisible = not path
        formula, formula2 = self._input(ID_FORMULA), self._input(ID_FORMULA2)
        formula2.isVisible = path
        if path:
            first, second = ('radius', 'angle in degrees') if polar else ('X offset', 'Y offset')
            formula.tooltip = f'Path: the {first} of copy i. Math in i, n, t = i/n, a, b, c and any user parameter.'
            formula2.tooltip = f'Path: the {second} of copy i; same variables as the formula above.'
        else:
            formula.tooltip = ('Math in i (copy number), n (copies), t = i/n, a, b, c and any user parameter. '
                               '^ is power. See Formula reference below.')
        self._input(ID_A_LEN).isVisible = not circular
        self._input(ID_A_ANG).isVisible = circular
        self._input(ID_ADVANCED_GROUP).isVisible = not sketch   # compute option / timeline group: features only

        # Vary each copy: the rotation reference is an axis (solid) or a sketch point (sketch);
        # scaling about a point is for bodies and profiles; depth is for profiles only.
        self._set_filters(rotation_axis, ROTATION_CENTRE_FILTERS if sketch else DIRECTION_FILTERS)
        self._set_filters(scale_point, SCALE_POINT_FILTERS)
        self._set_filters(target, TARGET_FILTERS)
        rotation_axis.commandPrompt = PROMPTS['rotation_centre' if sketch else 'rotation_axis']
        rotation_axis.tooltip = (
            'The sketch point every copy turns about; empty = the centre of the selected curves.' if sketch
            else 'The axis every copy turns about, where the original sits (a linear edge, sketch line or construction axis).')
        scale_point.isVisible = not sketch
        self._input(ID_DEPTH).isVisible = profiles
        self._input(ID_OPERATION).isVisible = profiles
        target.isVisible = profiles and self.operation != 0
        for sel in (rotation_axis, scale_point, target):
            sel.setSelectionLimits(0, 1)

    @staticmethod
    def _set_filters(sel: adsk.core.SelectionCommandInput, filters):
        sel.clearSelectionFilter()
        for f in filters:
            sel.addSelectionFilter(f)

    def _fill_presets(self, kind, select_id: str):
        """The presets of one pattern kind ('linear' | 'circular' | 'path'; a bool means
        circular / linear) with ``select_id`` selected (Custom when it is not among them)."""
        dd = adsk.core.DropDownCommandInput.cast(self.inputs.itemById(ID_PRESET))
        dd.listItems.clear()
        self.preset_ids = []
        presets = engine.presets_for(kind)
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
    def pattern_type(self) -> str:
        """'linear' | 'circular' | 'path'."""
        item = adsk.core.DropDownCommandInput.cast(self._input(ID_TYPE)).selectedItem
        index = item.index if item is not None else 0
        return PATTERN_KINDS[index] if 0 <= index < len(PATTERN_KINDS) else 'linear'

    @property
    def circular(self) -> bool:
        return self.pattern_type == 'circular'

    @property
    def path(self) -> bool:
        return self.pattern_type == 'path'

    @property
    def polar(self) -> bool:
        """Path given as radius / angle instead of X / Y."""
        return adsk.core.DropDownCommandInput.cast(self._input(ID_COORDS)).selectedItem.index == 1

    @property
    def object_type(self) -> str:
        return adsk.core.DropDownCommandInput.cast(self._input(ID_OBJECT_TYPE)).selectedItem.name

    @property
    def profiles_mode(self) -> bool:
        return self.object_type == OBJECT_PROFILES

    @property
    def formula2_text(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA2)).value

    @property
    def rotation_text(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_ROTATION)).value.strip()

    @property
    def size_text(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_SIZE)).value.strip()

    @property
    def depth_text(self) -> str:
        return adsk.core.StringValueCommandInput.cast(self._input(ID_DEPTH)).value.strip()

    @property
    def size_kind(self) -> str:
        index = adsk.core.DropDownCommandInput.cast(self._input(ID_SIZE_KIND)).selectedItem.index
        return SIZE_KINDS[index][1] if 0 <= index < len(SIZE_KINDS) else engine.SIZE_SCALE

    @property
    def operation(self) -> int:
        """0 new body, 1 join, 2 cut (index into OPERATIONS)."""
        index = adsk.core.DropDownCommandInput.cast(self._input(ID_OPERATION)).selectedItem.index
        return index if 0 <= index < len(OPERATIONS) else 0

    @property
    def transform_mode(self) -> bool:
        """The transform engine builds this pattern (see the module docstring)."""
        return self.path or bool(self.rotation_text) or bool(self.size_text) or self.profiles_mode

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
            flip=self.flip, a=self.a, b=self.b, c=self.c, extra_vars=self.user_parameter_vars(),
            path=self.path, formula2=self.formula2_text, polar=self.polar,
            rotation=self.rotation_text, size=self.size_text, size_kind=self.size_kind,
            # The Depth field is hidden unless Profiles are patterned; a stale value must not count.
            depth=self.depth_text if self.profiles_mode else '')

    def _selected(self, input_id):
        """The one entity in a (0 or 1)-limited selection input, else None."""
        sel = adsk.core.SelectionCommandInput.cast(self._input(input_id))
        return sel.selection(0).entity if sel.selectionCount >= 1 else None

    def reference_entity(self):
        return self._selected(ID_AXIS if self.circular else ID_DIRECTION)

    @property
    def direction2_entity(self):
        return self._selected(ID_DIRECTION2)

    @property
    def rotation_axis_entity(self):
        return self._selected(ID_ROTATION_AXIS)

    @property
    def scale_point_entity(self):
        return self._selected(ID_SCALE_POINT)

    @property
    def target_entity(self):
        return self._selected(ID_TARGET)

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
            self.on_object_type_changed()     # Profiles need a depth: the preview says so
        elif cid == ID_PRESET:
            self.dirty = True
            self.apply_preset(self.preset_id)
        elif cid in (ID_FORMULA, ID_MODE, ID_FORMULA2):
            self.dirty = True
            self._switch_to_custom_if_edited()
        elif cid == ID_COORDS:
            self.dirty = True
            self._switch_to_custom_if_edited()
            self._update_selection_inputs()     # radius / angle or X / Y in the tooltips
            self._show_notes(self.preset_id)
        elif cid in (ID_SIZE_KIND, ID_OPERATION):
            self.dirty = True
            self._update_selection_inputs()     # the target body shows for Join / Cut
        elif cid in (ID_COUNT, ID_A_LEN, ID_A_ANG, ID_B, ID_C, ID_FLIP, ID_OBJECTS, ID_DIRECTION, ID_AXIS,
                     ID_DIRECTION2, ID_ROTATION, ID_ROTATION_AXIS, ID_SIZE, ID_SCALE_POINT, ID_DEPTH, ID_TARGET):
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
        fills = [(ID_EXISTING, record.highlight),
                 (ID_OBJECTS, record.objects),
                 (ID_AXIS if self.circular else ID_DIRECTION, reference)]
        # The transform engine's extra references; only an input that is shown can take them
        # (a hidden input cannot get the focus addSelection needs).
        for input_id, entity in ((ID_DIRECTION2, record.direction2), (ID_ROTATION_AXIS, record.rotation_axis),
                                 (ID_SCALE_POINT, record.scale_point), (ID_TARGET, record.target)):
            if entity is not None and self._input(input_id).isVisible:
                fills.append((input_id, [entity]))
        pending = []
        for input_id, entities in fills:
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
        kind = self.pattern_type
        self._update_selection_inputs()
        current_preset = self.preset_id
        current_mode = self.mode
        self._fill_presets(kind, current_preset)
        self._fill_modes(self.circular, current_mode)
        self._show_notes(self.preset_id)

    def on_object_type_changed(self):
        sel = adsk.core.SelectionCommandInput.cast(self._input(ID_OBJECTS))
        sel.clearSelection()
        was_sketch = self._input(ID_DIRECTION).selectionFilters == list(SKETCH_DIRECTION_FILTERS)
        if was_sketch != self.sketch_mode:
            # model edges cannot drive sketch patterns and vice versa (nor can a sketch point
            # be a rotation axis, or a model vertex a sketch rotation centre)
            for input_id in (ID_DIRECTION, ID_AXIS, ID_DIRECTION2, ID_ROTATION_AXIS, ID_SCALE_POINT):
                adsk.core.SelectionCommandInput.cast(self._input(input_id)).clearSelection()
        self._update_selection_inputs()
        sel.hasFocus = True

    def apply_preset(self, preset_id: str):
        preset = engine.preset_by_id(preset_id) or engine.preset_by_id('custom')
        if preset.id == 'custom':
            self._show_notes(preset.id)
            return
        adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA)).value = preset.formula
        self._fill_modes(self.circular, preset.mode)
        if self.path:
            adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA2)).value = preset.formula2
            dd = adsk.core.DropDownCommandInput.cast(self._input(ID_COORDS))
            dd.listItems.item(1 if preset.polar else 0).isSelected = True
            self._update_selection_inputs()
        adsk.core.ValueCommandInput.cast(self._input(ID_A_LEN)).value = self.units.convert(preset.a_mm, 'mm', 'cm')
        adsk.core.ValueCommandInput.cast(self._input(ID_A_ANG)).value = math.radians(preset.a_deg)
        adsk.core.ValueCommandInput.cast(self._input(ID_B)).value = preset.b
        adsk.core.ValueCommandInput.cast(self._input(ID_C)).value = preset.c
        self._show_notes(preset.id)

    def _show_notes(self, preset_id: str):
        preset = engine.preset_by_id(preset_id) or engine.preset_by_id('custom')
        notes = preset.notes
        if self.path:
            # The formula inputs keep their fixed labels, so say here what each one gives.
            what = ('radius, <b>Second formula</b> = angle in degrees' if self.polar
                    else f'X offset, <b>Second formula</b> = Y offset ({self.length_unit})')
            notes = f'<b>Formula</b> = {what}.<br>{notes}'
        adsk.core.TextBoxCommandInput.cast(self._input(ID_NOTES)).formattedText = notes

    def _switch_to_custom_if_edited(self):
        preset = engine.preset_by_id(self.preset_id)
        if preset is None or preset.id == 'custom':
            return
        edited = self.formula_text.strip() != preset.formula
        if self.path:
            edited = edited or self.formula2_text.strip() != preset.formula2 or self.polar != preset.polar
        else:
            edited = edited or self.mode != preset.mode
        if edited:
            self._fill_presets(self.pattern_type, 'custom')
            self._show_notes('custom')

    def refresh_preview(self):
        preview = adsk.core.TextBoxCommandInput.cast(self._input(ID_PREVIEW))
        formula = adsk.core.StringValueCommandInput.cast(self._input(ID_FORMULA))
        error = False
        try:
            spec = self.spec()
            result = engine.compute_positions(spec)
            html = engine.preview_html(result, self.length_unit, spec.circular, spec.mode, spec.flip,
                                       path=spec.path, size_kind=spec.size_kind)
            if self.live and spec.count > MAX_MODEL_PREVIEW:
                html += f'<br><i>Model preview is off above {MAX_MODEL_PREVIEW} copies; the result is built on OK.</i>'
            problem = self._problem()
            if problem:
                html = engine.error_html(problem) + '<br>' + html
                error = True
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
        problem = self._problem()
        if problem:
            return False, problem
        try:
            engine.compute_positions(self.spec())
        except engine.FormulaError as e:
            return False, f'formula: {e}'
        return True, 'ok'

    def _problem(self):
        """What stops a Path / Vary each copy pattern from being built, as a message for the
        preview, or None. The plain requirements (objects, a direction or axis) are not
        repeated here: Fusion marks those inputs itself."""
        sketch, kind = self.sketch_mode, self.object_type
        if self.path and not sketch:
            first, second = self.reference_entity(), self.direction2_entity
            if second is None:
                return 'Path needs Direction 2 (the Y direction)'
            if first is not None:
                try:
                    placement.orthonormal_pair(placement.direction_of(first), placement.direction_of(second))
                except placement.GeometryError as e:
                    return str(e)
                except ValueError:
                    return 'Direction 2 must not be parallel to Direction 1'
        if self.transform_mode and kind in NATIVE_ONLY_TYPES:
            return 'Path, Rotation and Size need bodies, components, sketch curves or profiles'
        if self.rotation_text and not sketch and self.rotation_axis_entity is None:
            return 'Rotation needs a rotation axis'
        if self.size_text:
            if kind == 'Components':
                return 'Size does not apply to components'
            if kind in ('Bodies', OBJECT_PROFILES) and self.scale_point_entity is None:
                return 'Size needs a point to scale about'
            if self.size_kind == engine.SIZE_DIAMETER:
                if not all(_type_name(_native(e)) == 'SketchCircle' for e in self.selected_objects()):
                    return 'Diameter needs sketch circles'
        if kind == OBJECT_PROFILES:
            if not self.depth_text:
                return 'Profiles need a Depth formula'
            if self.operation != 0 and self.target_entity is None:
                return 'Join and Cut need a Target body'
        return None

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
        problem = self._problem()
        if problem:
            raise RuntimeError(problem)
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
            if self.transform_mode:
                return self._build_transformed(spec, result, entities, reference)
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

        Features (and occurrences) are deleted with the timeline rolled back to where they
        were, so the rebuilt ones (created at the marker) take the same place and anything
        after them stays after them; the build puts the marker back afterwards. They go in
        reverse order — highest copy first, and within a copy the Move before the Scale before
        the Copy / Extrude it depends on. A sketch pattern constraint leaves its copied geometry
        behind when deleted, so the copies are deleted as well; sketch copies made by the
        transform engine are plain sketch entities and are simply deleted.
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
        if record.kind == 'sketch_copies':
            for e in record.copies:
                try:
                    e.deleteMe()
                except Exception:
                    pass
            return
        timeline = self.design.timeline
        self._marker_before = timeline.markerPosition
        self._marker_was_at_end = timeline.markerPosition >= timeline.count
        copies = self._live_copies(record)
        timeline.markerPosition = min(_timeline_index(f) for f in copies)
        for feature in reversed(copies):             # sorted by copy number, discovery order within
            feature.deleteMe()

    def _live_copies(self, record) -> list:
        """The pattern's features as the timeline holds them now. The wrappers a record was
        made with can go stale between loading and building (seen in Fusion 2026-11-08: no
        name, no timelineObject), so the copies are looked up again by pattern id."""
        live = [e for e in _timeline_entities(self.design) if _attribute(e, 'pattern') == record.id]
        if live:
            live = sorted(_distinct(live), key=_copy_number)
            if len(live) != len(record.copies):
                _log(f'  live copies of {record.label}: {len(live)} in the timeline, {len(record.copies)} recorded')
            return live
        _log(f'  live copies of {record.label}: none found by id, using the recorded ones')
        return list(record.copies)

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

    # ---- the transform engine ----

    def _build_transformed(self, spec, result, entities, reference):
        """Place every copy by an explicit transform M_i = P_i · R_i (see the module docstring).

        Bodies: Copy/Paste → (Scale) → free Move. Components: a new occurrence per copy.
        Profiles: Extrude by the copy's depth → (Scale) → Move, then one Combine for Join /
        Cut. Sketch curves: ``Sketch.copy`` with the matrix in sketch space (see
        ``_build_sketch_copies``). Everything is named, tagged and grouped like the native
        pattern features.
        """
        items = list(entities)
        settings_json = json.dumps(self.collect_settings(with_tokens=True))
        if self.sketch_mode:
            return self._build_sketch_copies(spec, result, items, reference, settings_json)
        component = self.design.activeComponent
        timeline = self.design.timeline
        start_index = timeline.markerPosition
        group_name, n, kind = self.group_name, result.count, self.object_type
        matrices = self._solid_transforms(spec, result, reference)
        sizes = result.sizes or [None] * n
        depth_sign = self._depth_direction(items) if kind == OBJECT_PROFILES else 1
        created, tools = [], []
        for k, cells in enumerate(matrices, 1):
            if kind == 'Components':
                features = self._place_occurrences(component, items, cells, spec, result, k)
            else:
                if kind == OBJECT_PROFILES:
                    features, bodies = self._extrude_profiles(component, items, depth_sign * result.depths[k - 1],
                                                              spec, result, k)
                else:
                    features, bodies = self._copy_bodies(component, items, spec, result, k)
                features += self._size_and_move(component, bodies, cells, sizes[k - 1], spec, result, k)
                tools += bodies
                for body in bodies:
                    self._tag_member(body, k)
            for feature in features:
                self._tag(feature, k, n, group_name, settings_json)
            created += features
        if kind == OBJECT_PROFILES and self.operation != 0:
            combine = self._combine(component, tools)
            self._tag(combine, n, n, group_name, settings_json)
            created.append(combine)
        self._group_timeline(timeline, start_index, created, group_name)
        if self.editing:
            self._restore_marker()
        return created

    def _to_cm(self, length: float) -> float:
        return self.units.convert(length, self.length_unit, 'cm')

    def _solid_transforms(self, spec, result, reference) -> list:
        """M_i (16 cells, centimetres) for every copy of a body, component or profile pattern."""
        n = result.count
        rotations = [placement.identity()] * n
        if result.rotations:
            point, axis_dir = self._axis_vectors('Rotation axis', self.rotation_axis_entity)
            rotations = [placement.rotation(degrees, axis_dir, point) for degrees in result.rotations]
        if spec.path:
            u = self._direction_vector('Direction 1', reference)
            v = self._direction_vector('Direction 2', self.direction2_entity)
            try:
                u, v = placement.orthonormal_pair(u, v)
            except ValueError:
                raise RuntimeError('Direction 2 must not be parallel to Direction 1') from None
            placements = []
            for x, y in result.points:
                x, y = self._to_cm(x), self._to_cm(y)
                placements.append(placement.translation(*(x * u[j] + y * v[j] for j in range(3))))
        elif spec.circular:
            point, axis_dir = self._axis_vectors('Axis', reference)
            placements = [placement.rotation(angle, axis_dir, point) for angle in result.positions]
        else:
            u = self._direction_vector('Direction', reference)
            placements = [placement.translation(*(self._to_cm(d) * u[j] for j in range(3))) for d in result.positions]
        return [placement.multiply(p, r) for p, r in zip(placements, rotations)]

    @staticmethod
    def _direction_vector(label: str, entity):
        if entity is None:
            raise RuntimeError(f'Select the {label} first')
        try:
            return placement.direction_of(entity)
        except placement.GeometryError as e:
            raise RuntimeError(f'{label}: {e}') from None

    @staticmethod
    def _axis_vectors(label: str, entity):
        """(point, unit direction) of an axis entity: a straight line, or the axis of a circle,
        cylinder, cone or torus (circular edge, sketch circle, round face)."""
        if entity is None:
            raise RuntimeError(f'Select the {label} first')
        try:
            return placement.axis_of(entity)
        except placement.GeometryError as e:
            found = _round_axis(entity)
            if found is None:
                raise RuntimeError(f'{label}: {e}') from None
            return found

    def _creation_failed(self, spec, result, k) -> RuntimeError:
        n = result.count
        if spec.path:
            x, y = result.points[k - 1]
            where = f'({engine.format_number(x)}, {engine.format_number(y)}) {self.length_unit}'
        else:
            where = f'{engine.format_number(result.positions[k - 1])}{"°" if spec.circular else " " + self.length_unit}'
        return RuntimeError(f'Fusion could not create copy {k} of {n} at {where}')

    def _copy_bodies(self, component, items, spec, result, k):
        feature = component.features.copyPasteBodies.add(adsk.core.ObjectCollection.createWithArray(items))
        if feature is None:
            raise self._creation_failed(spec, result, k)
        return [feature], _items(feature.bodies)

    def _depth_direction(self, profiles) -> int:
        """Which way a positive depth goes: +1 along the profile's normal, -1 against it.

        A profile usually sits on a face of the target body, and its normal points away from
        the body. For *Cut* a positive depth goes into the target, for *Join* away from it
        (negative depths reverse that); the side the target is on is found by probing a point
        just off the profile along its normal. *New body* follows the normal.
        """
        target = self.target_entity
        if self.operation == 0 or target is None:
            return 1
        inside = adsk.fusion.PointContainment.PointInsidePointContainment
        try:
            profile = profiles[0]
            centroid, normal = _profile_in_model_space(profile)
            sides = {}
            for sign in (1, -1):
                probe = adsk.core.Point3D.create(centroid.x + sign * normal.x * 0.02,
                                                 centroid.y + sign * normal.y * 0.02,
                                                 centroid.z + sign * normal.z * 0.02)
                sides[sign] = target.pointContainment(probe) == inside
            _log(f'  depth direction: centroid=({centroid.x:.4g}, {centroid.y:.4g}, {centroid.z:.4g}) '
                 f'normal=({normal.x:.3g}, {normal.y:.3g}, {normal.z:.3g}) inside: +{sides[1]} -{sides[-1]}')
        except Exception:
            _log(f'_depth_direction failed, keeping the profile normal:\n{traceback.format_exc()}')
            return 1
        if sides[1] == sides[-1]:
            return 1                     # the target is on both sides, or on neither: nothing to go by
        towards_target = 1 if sides[1] else -1
        return towards_target if self.operation == 2 else -towards_target

    def _extrude_profiles(self, component, items, depth, spec, result, k):
        extrudes = component.features.extrudeFeatures
        extrude_input = extrudes.createInput(adsk.core.ObjectCollection.createWithArray(items),
                                             adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        distance = adsk.core.ValueInput.createByString(f'{abs(depth):.9g} {self.length_unit}')
        direction = (adsk.fusion.ExtentDirections.PositiveExtentDirection if depth > 0
                     else adsk.fusion.ExtentDirections.NegativeExtentDirection)
        extrude_input.setOneSideExtent(adsk.fusion.DistanceExtentDefinition.create(distance), direction)
        feature = extrudes.add(extrude_input)
        if feature is None:
            raise self._creation_failed(spec, result, k)
        return [feature], _items(feature.bodies)

    def _size_and_move(self, component, bodies, cells, size, spec, result, k) -> list:
        """Scale the copy's bodies about the scale point (when a size is given), then move them by M_i."""
        features = []
        collection = adsk.core.ObjectCollection.createWithArray(bodies)
        if size is not None:
            scales = component.features.scaleFeatures
            scale_input = scales.createInput(collection, self.scale_point_entity, adsk.core.ValueInput.createByReal(size))
            feature = scales.add(scale_input)
            if feature is None:
                raise self._creation_failed(spec, result, k)
            features.append(feature)
        moves = component.features.moveFeatures
        move_input = moves.createInput2(collection)
        move_input.defineAsFreeMove(placement.to_matrix3d(cells))
        feature = moves.add(move_input)
        if feature is None:
            raise self._creation_failed(spec, result, k)
        features.append(feature)
        return features

    def _place_occurrences(self, component, items, cells, spec, result, k) -> list:
        made = []
        for occurrence in items:
            transform = placement.multiply(cells, placement.from_matrix3d(occurrence.transform2))
            new = component.occurrences.addExistingComponent(occurrence.component, placement.to_matrix3d(transform))
            if new is None:
                raise self._creation_failed(spec, result, k)
            made.append(new)
        return made

    def _combine(self, component, tools):
        combines = component.features.combineFeatures
        combine_input = combines.createInput(self.target_entity, adsk.core.ObjectCollection.createWithArray(tools))
        combine_input.operation = OPERATIONS[self.operation][1]
        combine_input.isKeepToolBodies = False
        feature = combines.add(combine_input)
        if feature is None:
            raise RuntimeError(f'Fusion could not {OPERATIONS[self.operation][0].lower()} the copies '
                               f'into {_describe(self.target_entity)}')
        return feature

    def _tag(self, entity, k, n, group_name, settings_json):
        """Name a feature ``<group> k/n`` and add the attributes every copy carries. An
        occurrence keeps its own name (Occurrence.name is read-only)."""
        try:
            if _type_name(entity) != 'Occurrence':
                entity.name = f'{group_name} {k}/{n}'
        except Exception:
            pass
        try:
            entity.attributes.add(ATTRIBUTE_GROUP, 'settings', settings_json)
            entity.attributes.add(ATTRIBUTE_GROUP, 'copy', str(k))
            entity.attributes.add(ATTRIBUTE_GROUP, 'pattern', self._pattern_id)
        except Exception:
            pass

    def _tag_member(self, entity, k):
        """A body (or sketch copy) the engine made: clicking it finds the pattern."""
        try:
            entity.attributes.add(ATTRIBUTE_GROUP, 'member', self._pattern_id)
            entity.attributes.add(ATTRIBUTE_GROUP, 'copy', str(k))
        except Exception:
            pass

    @staticmethod
    def _group_timeline(timeline, start_index, created, group_name):
        end_index = timeline.markerPosition - 1
        if len(created) > 1 and end_index > start_index:
            try:
                group = timeline.timelineGroups.add(start_index, end_index)
                group.name = group_name
            except Exception:
                pass

    def _build_sketch_copies(self, spec, result, items, reference, settings_json):
        """Sketch curves through the transform engine: ``Sketch.copy`` of the selected entities
        with M_i in sketch coordinates, then Diameter / Scale factor applied to the copies.
        The copies are the pattern: they carry the attributes themselves."""
        items = [_native(e) for e in items]
        reference = _native(reference) if reference is not None else None
        sketch = getattr(items[0], 'parentSketch', None)
        if sketch is None:
            raise RuntimeError('Select sketch curves or sketch points to pattern')
        if any(not _same_sketch(getattr(e, 'parentSketch', None), sketch) for e in items):
            raise RuntimeError('All selected curves must be in the same sketch')
        if reference is not None and not _same_sketch(getattr(reference, 'parentSketch', None), sketch):
            raise RuntimeError('The direction / centre must be in the same sketch as the curves')
        n = result.count

        # The rotation centre: the chosen sketch point, else the middle of the selection.
        centre_entity = self.rotation_axis_entity
        if centre_entity is not None:
            centre_entity = _native(centre_entity)
            if not _same_sketch(getattr(centre_entity, 'parentSketch', None), sketch):
                raise RuntimeError('The rotation centre must be in the same sketch as the curves')
            try:
                cx, cy = placement.sketch_point_of(centre_entity)
            except placement.GeometryError as e:
                raise RuntimeError(f'Rotation centre: {e}') from None
        else:
            cx, cy = _sketch_centre(items)
        rotations = [placement.identity()] * n
        if result.rotations:
            rotations = [placement.rotation(degrees, (0.0, 0.0, 1.0), (cx, cy, 0.0)) for degrees in result.rotations]

        def line_direction():
            if reference is None:
                return (1.0, 0.0)                     # the sketch X axis
            try:
                return placement.sketch_direction_of(reference)
            except placement.GeometryError as e:
                raise RuntimeError(f'Direction: {e}') from None

        if spec.path:
            u = line_direction()
            v = (-u[1], u[0])                         # perpendicular in the sketch plane
            placements = []
            for x, y in result.points:
                x, y = self._to_cm(x), self._to_cm(y)
                placements.append(placement.translation(x * u[0] + y * v[0], x * u[1] + y * v[1], 0.0))
        elif spec.circular:
            try:
                px, py = placement.sketch_point_of(reference)
            except placement.GeometryError as e:
                raise RuntimeError(f'Centre: {e}') from None
            placements = [placement.rotation(angle, (0.0, 0.0, 1.0), (px, py, 0.0)) for angle in result.positions]
        else:
            u = line_direction()
            placements = [placement.translation(self._to_cm(d) * u[0], self._to_cm(d) * u[1], 0.0)
                          for d in result.positions]

        created = []
        for k, (p, r) in enumerate(zip(placements, rotations), 1):
            cells = placement.multiply(p, r)
            copies = sketch.copy(adsk.core.ObjectCollection.createWithArray(items), placement.to_matrix3d(cells))
            copies = _items(copies) if copies is not None else []
            if not copies:
                raise self._creation_failed(spec, result, k)
            if result.sizes:
                size = result.sizes[k - 1]
                if spec.size_kind == engine.SIZE_DIAMETER:
                    for copy in copies:
                        if _type_name(copy) == 'SketchCircle':
                            copy.radius = self._to_cm(size) / 2.0
                else:
                    x, y, _ = placement.apply(p, (cx, cy, 0.0))   # the rotation centre, moved by P_i
                    sketch.scale(copies, adsk.core.Point3D.create(x, y, 0.0), size)
            for copy in copies:
                try:
                    copy.attributes.add(ATTRIBUTE_GROUP, 'settings', settings_json)
                    copy.attributes.add(ATTRIBUTE_GROUP, 'pattern', self._pattern_id)
                except Exception:
                    pass
                self._tag_member(copy, k)
            created += copies
        return created

    def show_build_error(self, message: str):
        self.last_error = message
        self.refresh_preview()

    # ---- remembering values ----

    def collect_settings(self, with_tokens: bool = False) -> dict:
        """The dialog's values: the remembered-settings file, and (``with_tokens``) the JSON the
        transform engine stores on every copy, which also names the selected entities by
        token so the pattern can be loaded again without rolling the timeline."""
        s = {
            'pattern_type': self.pattern_type,
            'object_type': self.object_type,
            'count': self.count,
            'preset': self.preset_id,
            'mode': self.mode,
            'formula': self.formula_text,
            'coords': 'polar' if self.polar else 'xy',
            'formula2': self.formula2_text,
            'a_length': adsk.core.ValueCommandInput.cast(self._input(ID_A_LEN)).expression,
            'a_angle': adsk.core.ValueCommandInput.cast(self._input(ID_A_ANG)).expression,
            'b': adsk.core.ValueCommandInput.cast(self._input(ID_B)).expression,
            'c': adsk.core.ValueCommandInput.cast(self._input(ID_C)).expression,
            'flip': self.flip,
            'live': self.live,
            'compute': adsk.core.DropDownCommandInput.cast(self._input(ID_COMPUTE)).selectedItem.index,
            'group_name': self.group_name,
            'rotation': self.rotation_text,
            'size': self.size_text,
            'size_kind': self.size_kind,
            'depth': self.depth_text,
            'operation': self.operation,
        }
        if with_tokens:
            s['tokens'] = {
                'objects': [_token(e) for e in self.selected_objects()],
                'direction': _token(self._selected(ID_DIRECTION)),
                'direction2': _token(self._selected(ID_DIRECTION2)),
                'axis': _token(self._selected(ID_AXIS)),
                'rotation_axis': _token(self._selected(ID_ROTATION_AXIS)),
                'scale_point': _token(self._selected(ID_SCALE_POINT)),
                'target': _token(self._selected(ID_TARGET)),
            }
        return s

    def _apply_settings(self, s: dict, force_object_type: bool = False):
        """Fill the inputs from a settings dict; unknown keys (and ``tokens``) are ignored."""
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

        def set_text(input_id, text):
            adsk.core.StringValueCommandInput.cast(self._input(input_id)).value = str(text)

        kind = s.get('pattern_type')
        if kind in PATTERN_KINDS and kind != self.pattern_type:
            do(lambda: select(ID_TYPE, PATTERN_KINDS.index(kind)))
            do(self.on_type_changed)
        names = [name for name, _ in OBJECT_TYPES]
        # While a sketch is being edited, sketch curves are what the user is after (unless the
        # settings come from a saved pattern, which knows what it patterned).
        if s.get('object_type') in names and (force_object_type or not self.active_sketch):
            do(lambda: select(ID_OBJECT_TYPE, names.index(s['object_type'])))
            do(self.on_object_type_changed)
        do(lambda: setattr(adsk.core.IntegerSpinnerCommandInput.cast(self._input(ID_COUNT)), 'value', int(s['count'])))
        do(lambda: self._fill_presets(self.pattern_type, s.get('preset', 'custom')))
        do(lambda: self._fill_modes(self.circular, s.get('mode', engine.MODE_POSITION)))
        do(lambda: set_text(ID_FORMULA, s['formula']))
        if 'coords' in s:
            do(lambda: select(ID_COORDS, 1 if s['coords'] == 'polar' else 0))
        for key, input_id in (('formula2', ID_FORMULA2), ('rotation', ID_ROTATION), ('size', ID_SIZE), ('depth', ID_DEPTH)):
            if key in s:
                do(lambda key=key, input_id=input_id: set_text(input_id, s[key]))
        if 'size_kind' in s:
            do(lambda: select(ID_SIZE_KIND, [value for _, value in SIZE_KINDS].index(s['size_kind'])))
        if 'operation' in s:
            do(lambda: select(ID_OPERATION, int(s['operation'])))
        do(lambda: setattr(adsk.core.BoolValueCommandInput.cast(self._input(ID_FLIP)), 'value', bool(s['flip'])))
        do(lambda: setattr(adsk.core.BoolValueCommandInput.cast(self._input(ID_LIVE)), 'value', bool(s['live'])))
        do(lambda: select(ID_COMPUTE, int(s['compute'])))
        do(lambda: set_text(ID_GROUP_NAME, s['group_name']))
        do(self._update_selection_inputs)          # coordinates and operation change what is shown
        do(lambda: self._show_notes(self.preset_id))

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


def _items(collection) -> list:
    """A Fusion collection (ObjectCollection, BRepBodies, …) as a list."""
    try:
        return [collection.item(k) for k in range(collection.count)]
    except Exception:
        return list(collection)


def _xyz(point):
    return (float(point.x), float(point.y), float(point.z))


def _round_axis(entity):
    """(point, direction) of a circular edge, sketch circle or cylindrical / conical / toroidal
    face: the centre and normal of a Circle3D, or the origin and axis of the surface. None
    when the entity's geometry has neither."""
    for attr in ('worldGeometry', 'geometry'):
        try:
            geometry = getattr(entity, attr)
        except Exception:
            continue
        if geometry is None:
            continue
        if hasattr(geometry, 'center') and hasattr(geometry, 'normal'):
            return _xyz(geometry.center), _xyz(geometry.normal)
        if hasattr(geometry, 'origin') and hasattr(geometry, 'axis'):
            return _xyz(geometry.origin), _xyz(geometry.axis)
    return None


def _sketch_centre(entities):
    """(x, y) centre of the union of the entities' bounding boxes, in sketch coordinates."""
    lows, highs = [], []
    for e in entities:
        try:
            box = e.boundingBox
            lows.append((box.minPoint.x, box.minPoint.y))
            highs.append((box.maxPoint.x, box.maxPoint.y))
        except Exception:
            continue
    if not lows:
        return (0.0, 0.0)
    x0, y0 = min(p[0] for p in lows), min(p[1] for p in lows)
    x1, y1 = max(p[0] for p in highs), max(p[1] for p in highs)
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


# ---------------------------------------------------------------------------
# Finding a saved pattern again
# ---------------------------------------------------------------------------

class NotAPattern(ValueError):
    """The selected entity is not a copy made by Formula Pattern; the message says why."""


class PatternRecord:
    """A saved pattern, found from its copies.

    ``copies`` are the pattern features and occurrences (kind 'features'), sketch pattern
    constraints (kind 'sketch') or copied sketch entities (kind 'sketch_copies') that make
    it up; ``objects`` and ``reference`` are what it patterned and along / around what,
    ``direction2``, ``rotation_axis``, ``scale_point`` and ``target`` the transform engine's
    other references (None when unused); ``highlight`` is what to show the user (the
    features, or the copied sketch curves); ``position`` orders patterns the way the
    timeline does.
    """

    def __init__(self, pattern_id, settings, copies, kind, objects, reference, label, highlight, position):
        self.id = pattern_id
        self.settings = settings
        self.copies = copies
        self.kind = kind
        self.objects = objects
        self.reference = reference
        self.direction2 = None
        self.rotation_axis = None
        self.scale_point = None
        self.target = None
        self.label = label
        self.highlight = highlight
        self.position = position


def find_pattern(design, entity, known=()) -> PatternRecord:
    """The saved pattern that ``entity`` (a feature, an occurrence, a constraint, a patterned
    sketch curve, or a body / sketch copy the transform engine made) is a copy of. ``known``
    are the records already listed, searched first for sketch curves. Raises NotAPattern
    otherwise."""
    native = _native(entity)
    source = _pattern_source(native)
    if source is None:
        record = _pattern_containing(design, native, known)
        if record is not None:
            return record
        raise NotAPattern('This is not a copy made by Formula Pattern')
    # A copy carries 'pattern'; a body or sketch curve the transform engine made carries 'member'.
    pattern_id = _attribute(source, 'pattern') or _attribute(source, 'member')
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
    type_name = _type_name(source)
    if type_name.endswith('Constraint'):
        kind = 'sketch'
    elif type_name.startswith('Sketch') and type_name != 'Sketch':
        kind = 'sketch_copies'            # the transform engine's copies of sketch curves
    else:
        kind = 'features'                 # pattern features, or the engine's features and occurrences
    objects, reference = [], None          # filled by read_inputs() when the pattern is chosen
    n = len({_attribute(c, 'copy') for c in copies})    # the engine makes several features per copy
    count = f'{n} {"copy" if n == 1 else "copies"}'
    position = 10 ** 9            # sketch patterns and anything not in the timeline go last
    if kind in ('sketch', 'sketch_copies'):
        if kind == 'sketch':
            highlight = []
            for constraint in copies:
                try:
                    highlight.extend(list(constraint.createdEntities))
                except Exception:
                    pass
        else:
            highlight = list(copies)
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
    """Fill ``record.objects`` and ``record.reference`` (and the transform engine's other
    references) from its first copy.

    A pattern made by the transform engine names its inputs by entity token in its settings
    (``tokens``); those are looked up directly. A native pattern feature gives its
    inputEntities and direction back only while the timeline marker sits just before it
    (API reference; read elsewhere they came back empty, Fusion 2026-11-06), so the marker
    is rolled there for the read and put back afterwards. Sketch constraints can be read as
    they are.
    """
    source = record.copies[0]
    circular = _type_name(source).startswith('Circular')
    record.objects, record.reference = [], None
    record.direction2 = record.rotation_axis = record.scale_point = record.target = None
    tokens = record.settings.get('tokens') if isinstance(record.settings, dict) else None
    if isinstance(tokens, dict):
        _read_tokens(design, record, tokens)
        return
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


def _read_tokens(design, record, tokens: dict):
    """Resolve the entity tokens the transform engine stored (first live hit of each)."""
    def resolve(token):
        if not token:
            return None
        try:
            found = list(design.findEntityByToken(token))
        except Exception:
            return None
        return found[0] if found else None

    record.objects = [e for e in (resolve(t) for t in tokens.get('objects') or []) if e is not None]
    circular = record.settings.get('pattern_type') == 'circular'
    record.reference = resolve(tokens.get('axis' if circular else 'direction'))
    record.direction2 = resolve(tokens.get('direction2'))
    record.rotation_axis = resolve(tokens.get('rotation_axis'))
    record.scale_point = resolve(tokens.get('scale_point'))
    record.target = resolve(tokens.get('target'))
    _log(f'  read_inputs by token: objects={[_describe(e) for e in record.objects]} '
         f'reference={_describe(record.reference) if record.reference is not None else None}')


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


def _profile_in_model_space(profile):
    """(centroid, normal) of a sketch profile in model space. ``areaProperties().centroid``
    comes back in sketch coordinates (seen in Fusion 2026-11-08: z = 0 for a sketch on a
    face 10 mm up), so it goes through the sketch's ``sketchToModelSpace``; the normal is the
    sketch's X direction × Y direction, which is also the direction a positive extrude takes."""
    centroid = profile.areaProperties().centroid
    sketch = profile.parentSketch
    try:
        centroid = sketch.sketchToModelSpace(centroid)
        normal = sketch.xDirection.crossProduct(sketch.yDirection)
        if not normal.normalize():
            raise ValueError('degenerate sketch axes')
    except Exception:
        _log(f'  _profile_in_model_space: falling back to the profile plane:\n{traceback.format_exc()}')
        normal = profile.plane.normal
    return centroid, normal


def _timeline_index(feature) -> int:
    """Where a feature sits in the timeline. Inside a collapsed group the feature's own index
    is not available (the reference says -1; Fusion 2026-11 raises "InternalValidationError :
    res >= 0") and the group's index is the position. An expanded group has no row of its
    own, so it is collapsed first. When none of that works the timeline rows are scanned for
    the feature."""
    tl = feature.timelineObject
    try:
        group = tl.parentGroup
    except Exception:
        group = None
    tried = []
    for item, what in ((tl, 'feature'), (group, 'group')):
        if item is None:
            continue
        try:
            index = item.index
        except Exception as e:
            tried.append(f'{what}: {e}')
            continue
        if index is not None and index >= 0:
            return index
        tried.append(f'{what}: index {index}')
    if group is not None:
        try:
            if not group.isCollapsed:
                group.isCollapsed = True
                index = group.index
                if index is not None and index >= 0:
                    return index
                tried.append(f'collapsed group: index {index}')
        except Exception as e:
            tried.append(f'collapsed group: {e}')
    index = _scan_timeline(feature)
    if index is not None:
        return index
    _log(f'_timeline_index({_describe(feature)}) failed: {"; ".join(tried)}')
    raise RuntimeError('Formula Pattern could not find the saved pattern in the timeline')


def _scan_timeline(feature, design=None):
    """The row index of ``feature`` (or of the collapsed group holding it) found by walking
    the timeline, or None."""
    try:
        if design is None:
            design = adsk.fusion.Design.cast(_app.activeProduct)
        timeline = design.timeline
        for i in range(timeline.count):
            item = timeline.item(i)
            group = adsk.fusion.TimelineGroup.cast(item)
            if group is not None:
                members = [group.item(j).entity for j in range(group.count)]
            else:
                members = [item.entity]
            if any(_same_entity(m, feature) for m in members if m is not None):
                return i
    except Exception:
        _log(f'_scan_timeline({_describe(feature)}) failed:\n{traceback.format_exc()}')
    return None


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
