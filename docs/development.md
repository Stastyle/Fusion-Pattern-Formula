# Formula Pattern — Development guide

How the add-in is put together, how to test it without Fusion, and the
Fusion API behaviours that cost time to discover.

## Layout

```
FormulaPattern/                 the add-in (linked or copied into Fusion's AddIns folder)
  FormulaPattern.manifest       id, version, run-on-startup
  FormulaPattern.py             run()/stop() entry; reloads the modules on every Run
  command.py                    Fusion side: toolbar buttons, dialog, events, building the result
  placement.py                  Fusion side: vectors from entities, 4×4 matrices for the transform engine
  engine.py                     pure Python: formula evaluator, positions, presets, preview text
  settings.py                   last-used settings in ~/.fusion_formula_pattern.json
  resources/FormulaPattern/     16x16, 32x32, 64x64 button icons
tests/
  fake_adsk/adsk/               a small fake of adsk.core / adsk.fusion
  test_engine.py                engine tests (plain Python)
  test_placement.py             matrix and entity-geometry tests against the fake
  test_fake_adsk.py             tests of the fake itself (its maths and feature behaviour)
  test_command.py               dialog, toolbar and build tests against the fake
docs/
  user-guide.md                 for users
  development.md                this file
  superpowers/specs/…           the design document
install.ps1 / install.sh        link the add-in into Fusion's AddIns folder
```

## How it works

**engine.py** has no Fusion imports. `Formula` parses an expression with
`ast`, validates it against a whitelist (names, calls, arithmetic,
comparisons, conditionals; no attributes, subscripts or strings) and
evaluates it in floats with overflow and complex-result guards.
`compute_positions(PatternSpec)` runs it for the copies, items
`i = 2 … n + 1` (the original is item 1; `t = (i - 1) / n`), applies the
position / gap mode, flip and the 360° wrap, and reports errors per item.
Before 1.3 the first copy was `i = 1`: `renumber_from_copies()` rewrites
such a formula with `(i - 1)`, and `command._modern_settings()` applies it
to settings (saved patterns, the remembered values) that lack the
`numbering` key 1.3 writes.
`PRESETS` is the preset table; `preview_html` renders the preview box.

**command.py** owns everything Fusion-facing:

* `start()` / `stop()` register the command definition and put the button
  into `SolidCreatePanel` and `SketchCreatePanel`, after the native pattern
  controls when those ids are found.
* `Dialog` builds the inputs, reads them (`spec()`), updates the preview,
  validates, and builds the result. `build()` dispatches to
  `_build_features` (one native Rectangular / Circular Pattern feature per
  copy, quantity 2, extent = the computed position, grouped in the timeline)
  or `_build_sketch` (one sketch pattern constraint per copy). Every copy
  gets three attributes in group `FormulaPattern`: `settings` (the dialog
  values as JSON), `copy` (its number) and `pattern` (a uuid shared by all
  copies of one pattern).
* Editing: `list_patterns()` runs when the dialog is created and fills the
  *Saved pattern* drop-down (`ID_PATTERN_LIST`, first group of the dialog)
  with one `PatternRecord` per pattern id, found through
  `Design.findAttributes` and, as a backstop, by walking the timeline
  (groups included) and the active sketch's constraints. A record holds
  the copies, the objects and direction read back from the first copy, a
  label (group name and copy count, or sketch name for sketch patterns) and
  what to highlight. `choose_pattern()` selects the entry, shows the
  *Copies* selection input (`ID_EXISTING`) and the status text box
  (`ID_EDIT_STATUS`), and calls `load_pattern()`, which applies the
  settings and fills three selection inputs through `_fill_selection()`:
  the copies (features, or the copied sketch curves) into *Copies*, which
  is how Fusion highlights them, then the objects and the direction / axis,
  each converted to a root-context proxy by `_in_root_context()`. Every
  entity Fusion refused is kept in `_pending`; `is_valid()` (validateInputs)
  retries them twice and then sets `reselect_needed`, which the status line
  turns into a request to click them. Focus goes back to *Objects* at the
  end, and `_loading` keeps the input-changed handler from treating our own
  fills as clicks. A copy clicked into *Copies* goes through
  `find_pattern()` to `choose_pattern()`. `dirty` is set by any later input
  change; while a loaded pattern is `unchanged`, neither the preview nor OK
  rebuilds anything. `build()` clears the highlight (it would hold deleted
  entities) and calls `_remove_existing()` before creating the new copies:
  for features that rolls the timeline marker to the old copies' position,
  deletes them, builds (new features are inserted at the marker), and puts
  the marker back; for sketch constraints it deletes each constraint and
  its `createdEntities`. The edited pattern keeps its id.
* The handler classes map Fusion's events onto the dialog:
  commandCreated → `Dialog(cmd)` + handler registration; activate →
  `on_activate()`; inputChanged → `on_input_changed()`; validateInputs →
  `is_valid()`; executePreview → `build()` with `isValidResult = True`;
  execute → `build()`; destroy → save settings.

* The transform engine (1.2): `build()` goes to `_build_transformed()`
  whenever the pattern type is Path, a Rotation or Size formula is set, or
  the object type is Profiles. Every copy gets a rigid transform
  `M_i = P_i · R_i` (own rotation about the rotation axis, then the
  pattern placement: translation along the direction, rotation about the
  pattern axis, or `x·u + y·v` between the two path directions) built from
  plain 16-cell lists in `placement.py` and handed to Fusion as a
  `Matrix3D` only at the end. Bodies: CopyPasteBody → (Scale) → Move
  (`defineAsFreeMove`); components: `addExistingComponent` with
  `M_i · transform2`; profiles: Extrude (new body, signed depth) → (Scale)
  → Move, then one Combine for Join / Cut; sketch curves: `Sketch.copy`
  with the matrix in sketch space, then `radius` (Diameter) or
  `Sketch.scale`. Size cannot go into the Move matrix (Fusion rejects a
  scaling matrix), hence the separate Scale feature. The features carry the
  same attributes as native patterns; bodies and sketch copies made this
  way carry `member` = pattern id, and the settings JSON stored on the
  copies holds the `entityToken`s of every selection (`tokens`), which
  `read_inputs()` resolves with `Design.findEntityByToken` when editing.

The design documents explain the decisions behind this:
`docs/superpowers/specs/2026-11-06-formula-pattern-design.md` and
`docs/superpowers/specs/2026-11-08-path-and-vary-design.md`.

## Running the tests

```bash
python -m unittest discover -s tests -v
```

Plain Python 3.10+; nothing to install. `tests/fake_adsk` is put on
`sys.path` by the tests and stands in for the real `adsk` package, so
`command.py` is imported and driven exactly as Fusion would drive it:
`commandCreated` is fired, the command is "shown" (`cmd.show()` activates
it), inputs are changed, `executePreview` / `execute` are fired, and the
fake records the pattern features or sketch constraints that were created.

The fake deliberately reproduces the Fusion behaviours listed below (for
example, a unitless value input raises until the command is activated), so
a change that would break in Fusion fails here first.

Add a test before changing behaviour; every bug found in Fusion so far has a
test that reproduces it against the fake.

## Adding a preset

Append a `Preset` to `engine.PRESETS`:

```python
Preset(id='log', name='Logarithmic', kinds=('linear', 'circular'),
       mode=MODE_POSITION, formula='a * ln(1 + b * i) / ln(1 + b * n)',
       a_mm=100.0, a_deg=90.0, b=1.0, c=0.0,
       notes='<b>a</b> total extent · <b>b</b> curvature (bigger = more bunching at the start)'),
```

`kinds` controls which pattern types list it; `a_mm` / `a_deg` are the
defaults for `a` in each. The notes must mention every tunable the formula
uses (there is a test for that).

Adding a function: add it to `engine.FUNCTIONS` and mention it in
`engine.REFERENCE_HTML` and the user guide.

## Fusion API behaviours to know

All verified in Fusion during development (November 2026):

1. **Unitless `ValueCommandInput` (unit type `''`) cannot be read or written
   in `commandCreated`.** `isValidExpression` is False and `expression` /
   `value` raise `RuntimeError: 2 : InternalValidationError : res` until the
   command is activated. Inputs with a unit type work immediately. Hence
   `Dialog.on_activate()`: saved `a b c` values and the first preview wait
   for the `activate` event, and `_number()` tolerates a not-ready input.
2. **An exception in `commandCreated` after the inputs exist leaves an
   inert dialog**: it still opens, but handlers registered after the
   exception are missing, so the UI "does nothing". Register handlers before
   anything that can fail.
3. **The entry file runs with `__package__` unset** (module named after the
   folder, `__path__` set). Relative imports work through `__name__`, but
   code that uses `__package__` directly breaks. `FormulaPattern.py`
   resolves the package the way the import system does.
4. **One Python runtime for all add-ins**: Stop + Run re-reads the entry
   file but not the submodules. `FormulaPattern.py` purges the package's
   entries from `sys.modules` on Stop and on Run.
5. **Sketch pattern inputs reject `None` in `setDirectionOne/Two`**
   ("3 : invalid argument directionTwoEntity") although the documentation
   says null means the default axis. The direction *properties* start out
   null and that is fine; set `quantityOne/Two` and `distanceOne/Two`
   through the properties and only set a direction entity when there is
   one.
6. **Geometry selected while editing a sketch inside a component occurrence
   arrives as a proxy** and the sketch constraint API rejects it
   ("3 : invalid argument directionOneEntity"). Pass `entity.nativeObject`
   (falls back to the entity when it is already native). The feature path
   keeps the proxies, which is what the native pattern command wants.
7. **Toolbar ids** come from Fusion's own
   `<install>/Fusion/UI/FusionUI/Resources/Toolbar/TabToolbars.xml`:
   workspace `FusionSolidEnvironment`, tabs `SolidTab` / `SketchTab`,
   panels `SolidCreatePanel` / `SketchCreatePanel`, native buttons
   `PatternRectangular`, `RectangularSketchPatternCommand`. Grep that file
   rather than guessing.
8. **Fusion may spell the document unit by preference** (`in`, `inch`,
   `"`); `document_length_unit()` normalises it and falls back to the
   `distanceDisplayUnits` enum.

Fusion's own API errors look like `<code> : <text>`; `build()` appends a
description of the selected direction and objects to them so a report from a
user says what was passed.

The edit path (all seen in Fusion on 2026-11-06, driving the dialog with
the diagnostics log on; the fake models them this way):

9. **`TimelineObject.index` raises `2 : InternalValidationError : res >= 0`
   for a feature inside a collapsed group** (the reference says it returns
   -1). `_timeline_index()` tries the feature, then its `parentGroup`, and
   takes the first index that comes back. `Timeline.count` and
   `markerPosition` count a collapsed group as one item.
10. **A pattern feature gives `inputEntities` and `directionOneEntity` /
    `axis` back only while the timeline marker is just before it** (the
    reference says so for these properties; read with the marker at the
    end they came back empty, and the dialog loaded with no objects).
    `read_inputs()` sets `markerPosition` to the feature's (or group's)
    index, reads, and puts the marker back. The entities come back as
    root-context proxies when the copy was found that way ("via Fret
    Jig:1"), which is what the selection inputs want; `_in_root_context()`
    converts natives for the other cases.
11. **A feature created while the timeline is rolled back is inserted at
    the marker**, which then moves past it. This is what puts the rebuilt
    copies where the old ones were (24 copies replaced by 22 in the same
    group, OK, undo all fine).
12. **Deleting a sketch pattern constraint keeps the copied geometry**;
    `createdEntities` lists it, so the edit path deletes those too (from
    the reference; not yet exercised).
13. **`Design.findAttributes(group, name)`** returns the attributes with
    that name on every entity, with `parent` set (as a proxy for entities
    in a component); the edit path filters by value (the pattern id).
14. **`SelectionCommandInput.addSelection()`** works from `inputChanged`
    once the input has focus and the entity is in the root context;
    `_fill_selection()` gives the input focus first, checks the return
    value, and leftovers are retried from validateInputs. An invisible
    input cannot take focus, so the *Copies* input is shown before it is
    filled.
15. **A copied sketch curve lists its pattern constraint in
    `geometricConstraints`** (seen), which `_pattern_source()` uses.
    `find_pattern()` also matches the clicked entity's `entityToken`
    against the known patterns' copies and originals
    (`_pattern_containing()`), so recognition does not hang on that link.
    The sketch origin point sits where a copy easily lands and is the
    usual stray click; a stray click keeps the edit in progress and just
    reports itself (`_restore_highlight()` puts the Copies input back).
    Sketch editing was exercised end to end: 3 copies to 2, old copies
    gone, switching patterns by clicking a copy of the other one.

The transform engine (1.2; seen in Fusion on 2026-11-08 while building a
sunflower path of 40 bodies, a linear pattern of bodies with rotation, a
Profiles + Cut pattern and a sketch pattern with diameters, and editing
each of them; the fake models them this way):

16. **`CopyPasteBodies.add` + `MoveFeatures` with `defineAsFreeMove`** (a
    rotation + translation matrix) places body copies exactly; a native
    pattern of the same bodies is replaced in place by Copy / Move pairs
    when a Rotation formula is added to it. **`ExtrudeFeatures` (new body)
    → Move → one `CombineFeatures` cut** makes the wells, and *Join* /
    *New body* work the same way. **`Occurrences.addExistingComponent`**
    (one occurrence per copy, placed by `M_i · transform2`) and
    **`ScaleFeatures`** (about a point, before the Move) were verified live
    as well.
17. **`Profile.areaProperties().centroid` is in sketch coordinates** (the
    reference says "relative to the sketch origin"): a profile on a face
    10 mm up came back with z = 0. `_profile_in_model_space()` converts it
    with `Sketch.sketchToModelSpace` and takes the normal as
    `xDirection × yDirection` (model space; also the direction a positive
    extrude takes). `_depth_direction()` then probes a point 0.2 mm on
    each side with `BRepBody.pointContainment` to send a *Cut* into the
    target and a *Join* away from it.
18. **The feature wrappers a `PatternRecord` was made with can go stale
    between loading and rebuilding**: at build time the pattern's
    `RectangularPatternFeature`s had no name and no `timelineObject`, so
    `_timeline_index()` could not place them. `_live_copies()` looks the
    copies up again by their `pattern` attribute (`_timeline_entities`)
    right before deleting them; `_timeline_index()` also collapses an
    expanded group and scans the timeline rows as fallbacks.
19. **`CommandInput.name` and `Occurrence.name` are read-only**, so the
    dialog cannot relabel *Formula* / *Direction* / *Rotation axis* per
    mode; the second path formula is *Second formula* and a bold line in
    the notes box says what the two mean.
20. **`Sketch.copy(entities, Matrix3D)` takes the matrix in sketch
    coordinates** (verified on the XY plane and on a sketch on a
    construction plane tilted 30° about X, with a rotation + translation
    matrix; `Sketch.scale` about a sketch point works on the copies too) and
    **`SketchCircle.radius` is settable on the copies** (they carry no
    constraints).
21. Driving Fusion for these tests: single-click a text field, `ctrl+a`,
    type, then click another field to commit; `Tab` and triple-clicks sent
    keystrokes to the viewport, where single letters are command
    shortcuts (an *Align* dialog opened and terminated the command). The
    command search palette and the moment a dialog closes register as a
    "Chrome" window to the computer-use gate; the toolbar button works.
22. **An occurrence cannot be deleted while the timeline is rolled back to
    before it**: `Occurrence.deleteMe()` raised `InternalValidationError :
    Xl::Utils::findObjectPath(this, objPath)` (a rolled-back feature can be
    deleted). `_remove_existing()` therefore puts the marker just *after* a
    pattern's occurrences before deleting them; the marker then lands where
    they were, and `addExistingComponent` does work with the marker rolled
    back, so the rebuilt occurrences take the old place in the timeline.
23. **Pasted bodies get Fusion's own names** (`Peg (1)`, `Peg (1) (1)`,
    `Peg (2)` …); `_copy_bodies()` renames each copy `<original> (k)`.
24. **Sketch entities are not deleted from outside their sketch**:
    `deleteMe()` on the engine's sketch copies did nothing while no sketch
    (or another one) was being edited, so an edit built the new copies on
    top of the old ones. `list_patterns()` offers a sketch pattern only
    while its own sketch is being edited.
25. **`RectangularPatternFeatureInput` starts with `quantityTwo = 3`** (the
    UI's default) and no second direction, so a pattern of a body made 5
    bodies per copy, 3 on the copy and 2 on the original, all coincident
    and invisible (seen 2026-11-08). `_build_features()` sets
    `quantityTwo = 1`, as the sketch pattern input already did.
26. **Editing a sketch rolls the timeline back to the sketch** (the marker
    sat at the sketch while the dialog was open inside it). The design's
    patterns after the sketch are rolled back there: their features were
    still found, and an edit even rebuilt them in the rolled-back region,
    invisibly; a rolled-back occurrence's attributes come back with
    `parent = None`, so a component pattern cannot be found at all. And
    replacing a pattern from before the sketch would roll the marker back
    past the open sketch. So `list_patterns()` offers only the sketch's own
    patterns inside a sketch, and `on_existing_changed()` refuses a clicked
    copy of a design pattern there.
27. **A window selection in a sketch takes the circles' centre points too**
    (a left-drag in the viewport while a selection input is active), so
    `_problem()` lets sketch points through for *Diameter (circles)*; the
    build only sets the radius of circles anyway.
28. `tools/fusion-scripts/` holds the two scripts used for the live checks:
    `FPTestGeometry` makes the test design (block with a profile, peg,
    louvre, sketches, a component, a tilted sketch) and `FPInspect` dumps
    the timeline, every body's bounding box and volume, every occurrence's
    transform and every sketch's circles to `~/.fusion_fp_inspect.txt`, to
    compare with the expected transforms. Copy a folder into Fusion's
    `API/Scripts` folder and run it from Shift+S.

**Diagnostics log.** `command.py` appends what the edit path does to
`~/.fusion_formula_pattern.log` (emptied when the add-in starts): the
pattern chosen, what was read back, every `addSelection` and its result,
and why `is_valid()` said no while a pattern is loaded. Read it when the
dialog misbehaves inside Fusion; set `LOG_FILE = None` to turn it off (the
tests do).

## Checking API names offline

The authoritative stubs are installed with Fusion:

* Windows: `%APPDATA%\Autodesk\Autodesk Fusion 360\API\Python\defs\adsk\core.py` and `fusion.py`
* macOS: `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/Python/defs/adsk/`

A property is `def name(self)` and its setter `def name(self, value)`; grep
them before using a member you are not sure about.

## Installing for development

`install.ps1` creates a directory junction (`install.sh` a symlink) from
Fusion's AddIns folder to `FormulaPattern/`, so the checked-out code is what
Fusion runs. Re-running the script is safe. After editing, Stop + Run the
add-in in UTILITIES → ADD-INS; a traceback in a message box means the
module failed to import.

Remove `__pycache__` folders before committing (they are ignored by git but
Fusion writes them next to the sources).

## Release checklist

1. `python -m unittest discover -s tests` is green.
2. Version bumped in `FormulaPattern/FormulaPattern.manifest` and noted in
   `CHANGELOG.md`.
3. Tried in Fusion: open the dialog, change the object type, pattern a
   feature, pattern sketch curves, press OK, reopen and check the remembered
   values, load a saved pattern through *Saved pattern*, change its count
   and check it is replaced in place.
