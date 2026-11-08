# Changelog

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
