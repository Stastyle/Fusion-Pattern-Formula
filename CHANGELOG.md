# Changelog

## 1.3.0 — 2026-11-09

* **`i` is now the item number: the original is 1 and the copies are
  2 … Copies + 1** (before, the first copy was `i = 1`). Every formula,
  Rotation, Size and Depth included, is evaluated for the copies with these
  numbers, so a growth formula such as `i^1.4` or `a * i^1.2` already
  changes the first copy instead of repeating the original. `t = (i - 1) / n`
  is 0 at the original and 1 at the last copy; `n` is still the number of
  copies. The preview rows, the error messages and the Formula reference
  use the item numbers. The presets were rewritten to keep their geometry
  (frets: `a * (1 - 1 / 2^((i - 1) / 12))`, the nut being item 1 and item
  `i` fret `i - 1`).
* Patterns saved by earlier versions, and the remembered values, are
  converted when they are loaded: every `i` in their formulas becomes
  `(i - 1)`, so they rebuild exactly as before.

## 1.2.2 — 2026-11-08

Found while checking every remaining mode combination in Fusion, with the
resulting geometry compared to the expected numbers:

* **Linear patterns made five bodies per copy instead of one.** Fusion
  starts a `RectangularPatternFeatureInput` with the UI's default of three
  instances in the second direction and no second direction, so the extra
  instances sat on top of each other (4 copies of a louvre made 20 bodies,
  invisible as duplicates). The add-in now asks for one row only.
* Inside a sketch the *Saved pattern* list holds only that sketch's own
  patterns. Editing a sketch rolls the timeline back to it, so the design's
  later patterns are rolled back there (a component pattern cannot even be
  found) and replacing an earlier one would roll the timeline back past the
  open sketch. Clicking a copy of a design pattern inside a sketch says
  *Finish the sketch first*.
* *Diameter (circles)* accepts the circles' centre points in the selection,
  which a window selection always takes along; only the circles get the
  diameter.

Verified in Fusion: a native linear pattern of a body converted in place to
circular, to circular with a rotation about another axis (transform
engine) and back to native circular and native linear; a Profiles pattern
on a Path (grid) cut into a block with a depth formula; a sketch Path
(Archimedean spiral) of a circle with growing diameters; a circular pattern
of a component with a rotation about the X axis; the refusals for faces,
features and components; the *Saved pattern* list inside and outside a
sketch.

## 1.2.1 — 2026-11-08

Found while testing the rest of 1.2 in Fusion (component copies, the Scale
feature, sketch copies on a tilted sketch, *Join* and *New body*):

* Editing a component pattern failed with `InternalValidationError :
  findObjectPath`: an occurrence cannot be deleted while the timeline is
  rolled back to before it. The old occurrences are now deleted with the
  marker just after them, and the new ones are still built in their place.
* Copies of bodies are named after the original with their copy number
  (`Peg (1)`, `Peg (2)` …) instead of Fusion's `Peg (1) (1)`.
* A sketch pattern made by the transform engine was listed in the *Saved
  pattern* list outside its sketch, where OK could not delete the old
  copies and built the new ones on top of them. Sketch patterns are listed
  only while their own sketch is being edited, as in 1.1.

Verified in Fusion: a Path (grid) pattern of a component with a rotation
formula, rebuilt in place with more copies; a linear pattern of a body
with a growing scale factor, rebuilt with fewer copies; copies of a circle
and a rectangle on a sketch tilted 30° with rotation and scale about a
sketch point; a Profiles pattern joined into a block and then changed to
new bodies.

## 1.2.0 — 2026-11-08

* *Path* pattern type: two formulas give every copy's offset in a plane,
  as X / Y or radius / angle, between two directions (or the sketch axes).
  Presets: Archimedean spiral, Logarithmic spiral, Sunflower (phyllotaxis),
  Sine wave, Ellipse, Grid.
* *Vary each copy* group: a **Rotation** formula (degrees about an axis,
  applied before the copy moves), a **Size** formula (scale factor about a
  point, or the diameter of sketch circles) and, for the new *Profiles*
  object type, a **Depth** formula that extrudes the profile per copy with
  *New body*, *Join* or *Cut* into a target body.
* These are built from native Copy, Scale, Move, Extrude and Combine
  features (one occurrence per copy for components; transformed copies for
  sketch curves) in the usual timeline group, listed and editable through
  *Saved pattern* like every other pattern. Linear and circular patterns
  without variation are built exactly as before.
* The preview lists path points and every copy's turn, size and depth.
* With *Cut* a positive depth goes into the target body and with *Join*
  away from it, whichever way the sketch faces; *New body* follows the
  sketch normal.
* Editing finds a pattern's features again by id right before replacing
  them, so a pattern whose feature handles went stale while the dialog was
  open is still replaced in place.

Tested in Fusion: a sunflower path of 40 bodies, louvres with a rotation
formula (converted in place from a 1.1 linear pattern), QRD wells cut by a
Profiles pattern, and sketch circles with growing diameters.

## 1.1.0 — 2026-11-06

* Edit a saved pattern: the *Saved pattern* list at the top of the dialog
  names every pattern made by Formula Pattern in the design, by group name
  and number of copies, plus the sketch patterns of the sketch being
  edited. Picking one highlights its copies in the model and the timeline,
  loads its objects, direction or axis, count, preset, formula, values and
  flip, and a line under the list says what OK will do. After a change, OK
  replaces the old copies in place in the timeline with the same group
  name; sketch patterns are rebuilt without leaving the old copies behind.
  With nothing changed, OK leaves the model alone. *New pattern* makes a
  new one from the loaded values. While a pattern is picked, clicking a
  copy of another pattern into the *Copies* field switches to it; a click
  on anything else there is reported and leaves the edit as it was.
* The Preview group starts closed and opens by itself when there is an
  error to show.
* While editing, the add-in writes what it does to
  `~/.fusion_formula_pattern.log` (emptied at start-up) for bug reports.
* Every copy now carries a `pattern` attribute (the pattern's id) next to
  `settings` and `copy`. Patterns made with 1.0.0 have no id and cannot be
  loaded; the dialog says so.

## 1.0.0 — 2026-11-06

First release.

* Linear and circular patterns of bodies, faces, features and components
  where a formula gives the distance or angle of every copy, built as one
  native pattern feature per copy in a timeline group.
* Sketch curves patterned inside a sketch with native sketch pattern
  constraints; button in SKETCH → CREATE as well as SOLID → CREATE.
* Formula in `i`, `n`, `t`, `a`, `b`, `c` and the design's user parameters;
  *Distance from original* or *Gap to previous* modes; flip.
* Presets: Constant spacing, Guitar frets (12-TET), Growing gaps (geometric
  and arithmetic), Ease in & out (sine), Ease in (power), Fibonacci gaps,
  Golden angle.
* Live numeric preview with per-copy errors and warnings; live model preview
  (off above 100 copies).
* Last-used settings remembered.

Fixed while testing in Fusion:

* The dialog failed to open with `InternalValidationError` because unitless
  value inputs cannot be read before the command is activated; this also
  left the object-type drop-down inert.
* The add-in failed to start because Fusion runs the entry file with
  `__package__` unset.
* Sketch patterns inside a component failed with `invalid argument
  directionOneEntity` (occurrence proxies) and `directionTwoEntity`
  (an empty direction passed to `setDirectionTwo`).
