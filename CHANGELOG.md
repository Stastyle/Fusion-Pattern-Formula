# Changelog

## 1.1.0 — 2026-11-06

* Edit a saved pattern: the new *Saved pattern* list (under *Edit a saved
  pattern*) names every pattern made by Formula Pattern in the design, by
  group name and number of copies, plus the sketch patterns of the sketch
  being edited. Picking one loads its objects, direction or axis, count,
  preset, formula, values and flip, and highlights its copies (listed in the
  *Copies* field, where clicking any copy of a pattern picks it too). After
  a change, OK replaces the old copies in place in the timeline with the
  same group name; sketch patterns are rebuilt without leaving the old
  copies behind. With nothing changed, OK leaves the model alone. *New
  pattern* makes a new one from the loaded values.
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
