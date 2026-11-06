# Formula Pattern — Development guide

How the add-in is put together, how to test it without Fusion, and the
Fusion API behaviours that cost time to discover.

## Layout

```
FormulaPattern/                 the add-in (linked or copied into Fusion's AddIns folder)
  FormulaPattern.manifest       id, version, run-on-startup
  FormulaPattern.py             run()/stop() entry; reloads the modules on every Run
  command.py                    Fusion side: toolbar buttons, dialog, events, building the result
  engine.py                     pure Python: formula evaluator, positions, presets, preview text
  settings.py                   last-used settings in ~/.fusion_formula_pattern.json
  resources/FormulaPattern/     16x16, 32x32, 64x64 button icons
tests/
  fake_adsk/adsk/               a small fake of adsk.core / adsk.fusion
  test_engine.py                engine tests (plain Python)
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
`compute_positions(PatternSpec)` runs it for `i = 1 … n`, applies the
position / gap mode, flip and the 360° wrap, and reports errors per copy.
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

The design document explains the decisions behind this:
`docs/superpowers/specs/2026-11-06-formula-pattern-design.md`.

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

Taken from the API reference for the edit path, not yet exercised in Fusion
(the fake models them this way; if Fusion disagrees, fix the fake first):

9. **`TimelineObject.index` is -1 for a feature inside a collapsed group**
   (and for an expanded group). `_timeline_index()` falls back to the
   group's index. `Timeline.count` and `markerPosition` count a collapsed
   group as one item.
10. **A feature created while the timeline is rolled back is inserted at
    the marker**, which then moves past it. This is what puts the rebuilt
    copies where the old ones were.
11. **Deleting a sketch pattern constraint keeps the copied geometry**;
    `createdEntities` lists it, so the edit path deletes those too.
12. **`Design.findAttributes(group, name)`** returns the attributes with
    that name on every entity, with `parent` set; the edit path filters by
    value (the pattern id). Seen working in Fusion: the list fills.
13. **`SelectionCommandInput.addSelection()` into an input without focus
    did nothing** (seen 2026-11: the inputs stayed empty and OK stayed
    off, with no error). `_fill_selection()` gives the input focus first,
    checks the return value, and the leftovers are retried from
    validateInputs, which the reference names as a good place for it. An
    invisible input cannot take focus, so the *Copies* input is shown
    before it is filled.
14. **Selection inputs want root-context proxies** for geometry in a child
    component (what the user's click hands out), while a pattern feature
    reads its `inputEntities` back in its own component. `_in_root_context()`
    converts with `createForAssemblyContext` through
    `rootComponent.allOccurrencesByComponent`. From the reference; the
    fake models it.

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
