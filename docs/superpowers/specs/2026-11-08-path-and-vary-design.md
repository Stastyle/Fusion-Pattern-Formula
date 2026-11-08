# Formula Pattern 1.2 — Path patterns and per-copy rotation, size and depth

Date: 2026-11-08. Extends `2026-11-06-formula-pattern-design.md`; everything there
still holds unless this document says otherwise.

## Intent

Until now a formula could only decide *where* each copy goes, along one direction or
around one axis. This release lets formulas decide more about every copy:

* **Path** (a new pattern type): two formulas give the position of copy *i* in a plane,
  as X / Y or as radius / angle. Spirals, sine waves, sunflower (phyllotaxis) layouts,
  ellipses, grids.
* **Rotation**: a formula in degrees turns every copy about an axis before it is moved
  into place. Louvres that twist along a wall, blades with changing pitch.
* **Size**: a formula scales every copy (scale factor), or, for sketch circles, sets its
  diameter directly. Drill indexes, tolerance test holes and pins, grilles that fade.
* **Depth**: for a new object type, *Profiles*, each copy is the selected sketch profile
  extruded by its own formula depth and moved into place. QRD acoustic diffusers,
  stepped terraces, bar charts in wood.

Everything stays parametric and native: the copies are ordinary Fusion features in one
timeline group, editable afterwards through the dialog's *Saved pattern* list like the
patterns of 1.1.

## Goals

* The four features above for the object kinds that Fusion lets us transform freely:
  bodies, components (occurrences), sketch curves, and profiles (Depth).
* The same look and feel: live numeric preview per copy, live model preview, per-copy
  error messages before OK, settings remembered, saved patterns editable.
* No regression for Linear / Circular patterns of faces, features, bodies and components
  without variation: those keep using native pattern features exactly as in 1.1.

## Non-goals

* Rotation, Size and Path for *faces* and *features* as objects (a native pattern feature
  cannot rotate or scale individual copies; the dialog says so and asks for bodies,
  components, sketch curves or profiles).
* Size for components (an occurrence transform cannot scale).
* Three-dimensional paths (a Z formula). The two directions span a plane.
* Formulas of their own for the original (copy 0). The original is left alone in every
  mode; for Profiles that means the user extrudes the original profile themselves.

## Anti-goals

* A dialog that grows into a wall of inputs. The new fields live in one collapsed group,
  *Vary each copy*, and the Path type only swaps the single formula field for two.
* Silent fallbacks. If Fusion cannot do what was asked (scale a component, rotate a face
  pattern), the preview says why and OK stays disabled.

## How it is built

### The two build engines

`command.Dialog.build()` keeps dispatching to the existing `_build_features` (native
Rectangular / Circular Pattern features, quantity 2 per copy) and `_build_sketch` (sketch
pattern constraints) whenever the pattern is Linear or Circular, nothing varies, and the
object type is not Profiles. That path is untouched.

Otherwise it uses the new **transform engine**, `_build_transformed()`, which places every
copy by an explicit rigid transform. The transform of copy *i* is

```
M_i = P_i · R_i
```

* `R_i` — the copy's own rotation: `Rotation` formula value (degrees) about the *Rotation
  axis* (solid) or about the rotation centre (sketch), applied where the original sits.
  Identity when Rotation is empty.
* `P_i` — the pattern placement: Linear → translation by `d_i · u` (u = unit vector of
  the direction entity, `d_i` the formula offset, sign already flipped by the engine);
  Circular → rotation by `angle_i` about the pattern axis; Path → translation by
  `x_i · u + y_i · v` (u from *Direction 1*, v from *Direction 2* made perpendicular to u
  by Gram-Schmidt; in a sketch u, v are the sketch X / Y axes, or the chosen sketch line
  and its in-plane perpendicular).

Size is not part of `M_i`: Fusion's Move feature rejects a matrix with scaling. Size is
applied by a separate step before the move (a Scale feature about the *Scale about*
point for bodies; `Sketch.scale()` about the rotation centre, or `SketchCircle.radius`
for Diameter, in a sketch).

Per object kind, one copy is built as:

| Object type | Steps per copy (all features named `<group> k/n`, attributes added, grouped) |
|---|---|
| Bodies | `copyPasteBodies.add(body)` → (Size: `scaleFeatures` about the scale point) → `moveFeatures.createInput2(bodies)`, `defineAsFreeMove(M_i)`, `add` |
| Components | `occurrences.addExistingComponent(occ.component, M_i · occ.transform2)` (one occurrence; no Size) |
| Profiles | `extrudeFeatures.createInput(profiles, NewBodyFeatureOperation)`, `setOneSideExtent(DistanceExtentDefinition.create(depth_i), Positive/NegativeExtentDirection)` → (Size: scale about the scale point) → Move by `M_i`. After all copies, when *Operation* is Join or Cut: one `combineFeatures` with the *Target body* as target and every moved body as tool, `isKeepToolBodies = False` |
| Sketch curves | `sketch.copy(entities, M_i in sketch space)` → Size: Diameter sets `radius` of every copied circle; Scale factor calls `sketch.scale(copies, centre_i, factor)` where `centre_i` is the rotation centre moved by `P_i` |

The features (and occurrences) of all copies go into one timeline group named after
*Timeline group*, as before. Geometry the engine makes (bodies, sketch copies) also gets
attributes so a click on it finds the pattern (see Editing).

Negative depth extrudes in the negative direction with `abs(depth)`. A depth of 0, a
size ≤ 0 or a Path point that lands on the original are errors named by copy, like the
existing "lands on the original" check.

### Geometry helpers — `FormulaPattern/placement.py` (new, Fusion-facing, tested against the fake)

Pure functions that turn Fusion entities into vectors and build matrices. Matrices are
composed as plain 4×4 Python lists (row-major, 16 floats, same layout as
`Matrix3D.asArray()`), and only turned into an `adsk.core.Matrix3D` at the end with
`setWithArray`, so nothing depends on `Matrix3D.transformBy()` order semantics.

```python
def direction_of(entity) -> (x, y, z)        # unit vector: linear BRepEdge (geometry Line3D start→end),
                                             # ConstructionAxis (InfiniteLine3D.direction), SketchLine (worldGeometry)
def axis_of(entity) -> ((ox, oy, oz), (dx, dy, dz))   # a point on the axis and its unit direction, same kinds
def point_of(entity) -> (x, y, z)            # BRepVertex.geometry, SketchPoint.worldGeometry, ConstructionPoint.geometry
def sketch_direction_of(line) -> (x, y)      # unit vector in sketch space from SketchLine.geometry
def sketch_point_of(point) -> (x, y)         # SketchPoint.geometry
def orthonormal_pair(u, v) -> (u, v')        # Gram-Schmidt; raises ValueError('parallel') when |u × v| < 1e-9
def identity() -> cells
def translation(dx, dy, dz) -> cells
def rotation(angle_deg, axis_dir, axis_point) -> cells     # Rodrigues, about an axis through a point
def multiply(a, b) -> cells                  # a · b  (apply b first, then a)
def apply(cells, point) -> (x, y, z)
def to_matrix3d(cells) -> adsk.core.Matrix3D
def from_matrix3d(m) -> cells
```

Lengths are in centimetres (Fusion's internal unit) in every helper; `command.py`
converts the document-unit formula results with `unitsManager.convert(value,
length_unit, 'cm')` before building vectors.

Rotation axis for sketch copies is the sketch normal through the rotation centre; the
rotation centre is the selected *Rotation centre* sketch point, else the centre of the
union of the selected entities' `boundingBox` (sketch space).

### Engine — `FormulaPattern/engine.py`

`PatternSpec` grows optional fields (defaults keep every existing caller working):

```python
path: bool = False          # pattern type Path
formula2: str = ''          # Y (or angle) formula
polar: bool = False         # Path given as radius / angle (degrees) instead of x / y
rotation: str = ''          # degrees per copy; '' = none
size: str = ''              # '' = none
size_kind: str = SIZE_SCALE # SIZE_SCALE = 'scale' | SIZE_DIAMETER = 'diameter'
depth: str = ''             # document units; '' = none
```

`PatternResult` grows:

```python
values2: List[float]                 # raw second formula values (Path)
points: List[Tuple[float, float]]    # Path: (x, y) offset of copy 1..n, after polar → cartesian and flip
rotations: List[float]               # degrees; [] when Rotation is empty
sizes: List[float]                   # scale factors or diameters; [] when Size is empty
depths: List[float]                  # [] when Depth is empty
```

`compute_positions(spec)`:

* Path: evaluate both formulas per copy (mode is ignored: positions only), polar →
  `(r cos θ, r sin θ)` with θ in degrees, flip negates y. `positions[i]` holds the
  distance from the original (`hypot`), `extent` the largest. A point within
  `ZERO_TOLERANCE` of (0, 0) is "Copy i lands on the original". Duplicate warnings use
  the points.
* Rotation / Size / Depth: evaluated per copy with the same environment (`i n t a b c` +
  user parameters). Errors read `Rotation, copy i = 3: …`. Size must be > 0
  ("Copy 3: size must be greater than 0"); depth must not be 0 ("Copy 3 has depth 0").
  Rotation has no restriction.

`Preset` grows `formula2: str = ''` and `polar: bool = False`; `kinds` may contain
`'path'`. `presets_for(kind)` accepts `'linear' | 'circular' | 'path'` (a bool still
means circular / linear). Path presets (a is a length, b and c plain numbers):

| id | name | polar | formula (x or r) | formula2 (y or θ) | a_mm | b | c |
|---|---|---|---|---|---|---|---|
| custom | Custom | no | `a * i` | `0` | 10 | 0 | 0 |
| spiral | Archimedean spiral | yes | `a * t` | `b * i` | 100 | 30 | 0 |
| log_spiral | Logarithmic spiral | yes | `a * b^i` | `c * i` | 5 | 1.15 | 25 |
| phyllotaxis | Sunflower (phyllotaxis) | yes | `a * sqrt(i)` | `i * 137.508` | 5 | 0 | 0 |
| sine | Sine wave | no | `a * i` | `b * sin(2 * pi * t * c)` | 10 | 20 | 1 |
| ellipse | Ellipse | no | `a * cos(2 * pi * t)` | `b * sin(2 * pi * t)` | 50 | 30 | 0 |
| grid | Grid (b per row) | no | `a * (i % b)` | `a * floor(i / b)` | 10 | 5 | 0 |

(The grid uses `i`, not `i - 1`: the original occupies the first cell, so copy 1 must
not land on it.)

Notes name every tunable (the existing test enforces it). The `golden_angle` circular
preset stays.

`preview_html(result, unit, circular, mode, flip, max_lines, path=False, size_kind=SIZE_SCALE)`:
Path lines read `i = 3: (12.5, 4.1) mm`; every line appends what varies:
`· turn 30°`, `· ×1.2` or `· ⌀8 mm`, `· depth 8 mm`. The header for Path says
`farthest copy 25.3 mm`.

`REFERENCE_HTML` gains a short **Vary each copy** paragraph with examples:
rotation `b * i` (degrees), size `1 + 0.1 * i`, diameter `a + 0.1 * i`, QRD depth
`a * ((i * i) % 7) / 7`.

### Dialog — `command.py`

New input ids and constants:

```python
ID_DIRECTION2 = 'fpDirection2'       # Path, solid: second direction
ID_COORDS = 'fpCoords'               # Path: 'X / Y' | 'Radius / Angle'
ID_FORMULA2 = 'fpFormula2'
ID_VARY_GROUP = 'fpVaryGroup'        # group "Vary each copy", collapsed
ID_ROTATION = 'fpRotation'           # string input, degrees formula, '' = none
ID_ROTATION_AXIS = 'fpRotationAxis'  # solid: 'Rotation axis' (DIRECTION_FILTERS); sketch: 'Rotation centre' (SketchPoints)
ID_SIZE = 'fpSize'                   # string input, '' = none
ID_SIZE_KIND = 'fpSizeKind'          # 'Scale factor' | 'Diameter (circles)'
ID_SCALE_POINT = 'fpScalePoint'      # solid: 'Scale about' ('Vertices', 'SketchPoints', 'ConstructionPoints')
ID_DEPTH = 'fpDepth'                 # string input, document units, Profiles only
ID_OPERATION = 'fpOperation'         # 'New body' | 'Join' | 'Cut', Profiles only
ID_TARGET = 'fpTarget'               # 'Target body' ('SolidBodies'), Join / Cut only
OBJECT_PROFILES = 'Profiles'         # OBJECT_TYPES += ((OBJECT_PROFILES, ('Profiles',)),)
PATTERN_TYPES = ('Linear', 'Circular', 'Path')
```

Layout changes:

* *Pattern type* gets a third entry, **Path**. `Dialog.pattern_type` → `'linear' |
  'circular' | 'path'`; `circular` stays a property (`pattern_type == 'circular'`); new
  property `path`.
* With Path: *Direction* is labelled `Direction 1 (X)`, *Direction 2 (Y)* appears below it
  (solid only; required; `DIRECTION_FILTERS`), *Axis* hidden, *Formula gives* hidden,
  *Coordinates* drop-down shown above the formulas, the *Formula* field is labelled
  `X formula` / `Radius formula` and *Y formula* / *Angle formula* (`ID_FORMULA2`) is
  shown under it. Presets list `presets_for('path')`. In a sketch, Direction 1 is
  optional (sketch X axis) and Direction 2 is hidden (perpendicular in the sketch).
* Group **Vary each copy** (`ID_VARY_GROUP`, after the Formula group, `isExpanded =
  False`): `Rotation` + `Rotation axis` / `Rotation centre`; `Size` + `Size gives` +
  `Scale about`; `Depth` + `Operation` + `Target body`. Depth, Operation and Target are
  visible only for Profiles; Target only for Join / Cut; Scale about only for solid
  object types; Rotation axis / centre filters follow sketch mode. All selection inputs
  in this group have limits (0, 1) and are hidden when not applicable (a hidden input
  must not demand a selection).
* Tooltips say what each field does in one sentence, including the units
  (degrees; factor or document units; document units).

Reading: `spec()` fills the new `PatternSpec` fields from the inputs (`rotation`, `size`,
`depth` are the stripped strings; empty = off). New properties: `direction2_entity`,
`rotation_axis_entity`, `scale_point_entity`, `target_entity`, `operation` (0 new body,
1 join, 2 cut), `size_kind`, `polar`, `transform_mode` (True when path, or
rotation/size non-empty, or object type Profiles).

Validation (`_validity`), in this order after the existing checks:

* Path solid: Direction 1 and Direction 2 selected and not parallel ("Direction 2 must
  not be parallel to Direction 1").
* `transform_mode` with object type Faces or Features: "Path, Rotation and Size need
  bodies, components, sketch curves or profiles".
* Rotation non-empty, solid: Rotation axis selected.
* Size non-empty: Components → "Size does not apply to components"; Bodies / Profiles →
  Scale about selected; Diameter kind → every object is a SketchCircle ("Diameter needs
  sketch circles").
* Profiles: Depth non-empty ("Profiles need a Depth formula"); Join / Cut → Target body.
* Then `engine.compute_positions(self.spec())` as now.

`on_input_changed`: the new ids mark `dirty` and refresh the preview; `ID_PRESET` for Path
also fills `ID_FORMULA2` and selects the coordinates; editing formula 2 or the
coordinates switches the preset to Custom like formula 1 does; `ID_OBJECT_TYPE`,
`ID_TYPE` and `ID_SIZE_KIND`, `ID_OPERATION` re-run `_update_selection_inputs()`
(which now also handles visibility of everything above).

Settings (`collect_settings` / `_apply_settings`) gain `pattern_type: 'path'`, `coords`
(`'xy' | 'polar'`), `formula2`, `rotation`, `size`, `size_kind` (`'scale' |
'diameter'`), `depth`, `operation` (index). Only the attribute JSON stored on the copies
(not the remembered-settings file) also carries `tokens`: `{'objects': [...],
'direction': tok, 'direction2': tok, 'axis': tok, 'rotation_axis': tok, 'scale_point':
tok, 'target': tok}` with `entityToken`s (null where empty). `_apply_settings` ignores
unknown keys.

### Editing patterns made by the transform engine

* Attributes: every created feature and occurrence gets `settings`, `copy` (k) and
  `pattern` (id) as before; the Combine gets `copy = str(n)`. Bodies the engine creates
  and sketch copies get `member` = pattern id and `copy` = k (so a clicked body or curve
  finds the pattern: `find_pattern()` accepts `pattern` *or* `member`).
* `EXISTING_FILTERS` gains `'Bodies'` and `'Occurrences'`.
* `_record()`: `kind` is `'sketch'` for pattern constraints, `'sketch_copies'` when the
  copies are sketch entities, else `'features'` (features and occurrences; both have
  `timelineObject` and `deleteMe`). The copy count in the label is the number of distinct
  `copy` values. Label for sketch copies: `'<Sketch> sketch pattern (n copies)'`.
* `read_inputs()`: when `settings['tokens']` exists, every input is resolved with
  `design.findEntityByToken(token)` (first hit) and `_in_root_context()`; `record`
  gains `direction2`, `rotation_axis`, `scale_point`, `target`. Without tokens the
  marker-rolling read of 1.1 is used. `load_pattern()` fills the matching inputs.
* `_remove_existing()`: `'features'` deletes copies in reverse timeline order (highest
  `copy`, then reverse discovery order) with the marker rolled back as today;
  `'sketch_copies'` deletes the entities. Rebuilding keeps the id.

### Fake adsk — `tests/fake_adsk`

Adds, mirroring the stubs: `core.Matrix3D` (real 4×4 maths: `create`, `setToIdentity`,
`setToRotation(angle, axis, origin)`, `setWithCoordinateSystem`, `setWithArray`,
`asArray`, `getCell`, `setCell`, `copy`, `invert`, `transformBy` (self = matrix · self),
`translation`, `isEqualTo`), `core.Vector3D`, `core.Point3D` (both with `transformBy`;
a vector ignores translation), `core.BoundingBox3D`, `core.Line3D`, `core.InfiniteLine3D`.

`fusion`: `FeatureOperations` (Join 0, Cut 1, Intersect 2, NewBody 3, NewComponent 4),
`ExtentDirections` (Positive 0, Negative 1, Symmetric 2), `DistanceExtentDefinition.create`,
`BRepBody(name, component, origin=(0,0,0))` with `_transform` (cells), `_scale`,
`location()` test helper (origin through `_transform`), `boundingBox`,
`physicalProperties.centerOfMass`, `attributes`, `entityToken`, `deleteMe`,
`createForAssemblyContext`; `BRepBodies`; `Entity(..., start=, end=)` so a linear edge /
construction axis has `geometry` (Line3D / InfiniteLine3D) and a vertex `geometry`
(Point3D); `CopyPasteBodies.add(body)` → `CopyPasteBody` feature with `bodies` (one new
body at the same place) and `sourceBody`; `MoveFeatures.createInput2` / `MoveFeatureInput.
defineAsFreeMove` (raises `3 : invalid argument transform` when the 3×3 part is not
orthonormal) / `defineAsRotate` / `defineAsTranslateXYZ`; `MoveFeatures.add` applies the
matrix to every body's `_transform`; `ScaleFeatures.createInput(entities, point, factor)`
/ `add` multiplies `_scale`; `ExtrudeFeatures.createInput(profile, operation)` /
`ExtrudeFeatureInput.setOneSideExtent` / `add` → `ExtrudeFeature` with one new body at the
profile centroid and `depth` (signed cm) recorded; `CombineFeatures.createInput(target,
tools)` / `add` (tools deleted unless `isKeepToolBodies`); `Component.occurrences.
addExistingComponent(component, transform)` → `Occurrence` with `transform2`,
`timelineObject`, `attributes`, `entityToken`, `deleteMe`, `component`;
`Design.add_component` goes through it; `Profile(sketch, name, centroid)` with
`areaProperties().centroid`, `boundingBox`, `parentSketch`, `entityToken`;
`Sketch.profile(name, centroid)`; sketch entities with geometry: `Sketch.line(name,
start=(0,0), end=(1,0))`, `point(name, at=(x,y))`, `circle(name, centre=(x,y),
radius=1)`; `SketchEntity.boundingBox`, `geometry`, `worldGeometry` (sketch = model
space in the fake), `SketchCircle.radius` settable; `Sketch.copy(collection, matrix)`
→ new entities of the same types with transformed geometry, `Sketch.scale(entities,
point, factor)`; `Sketch.xDirection`, `yDirection`, `origin`;
`Design.findEntityByToken(token)` over a token registry filled by everything that gets
an `entityToken` (cleared by `Application.reset()`). Every new feature goes through the
timeline's `_insert` like the pattern features, so groups, marker rolling and `names()`
keep working.

## What is left to the builder

* Exact wording of tooltips and the user-guide text.
* Internal structure of `_build_transformed()` (one method per object kind is expected).
* Test names and fixtures, as long as every behaviour above has a test against the fake
  and the full suite passes.

## Verification in Fusion

Before release, in the user's open Fusion: Path pattern of a body (sine or phyllotaxis),
Path pattern of sketch circles with Diameter, a Linear pattern of a body with Rotation,
Profiles with Depth (QRD-style) and Cut into a block, then reopen each from *Saved
pattern*, change a value, OK, and check the copies were replaced in place.
