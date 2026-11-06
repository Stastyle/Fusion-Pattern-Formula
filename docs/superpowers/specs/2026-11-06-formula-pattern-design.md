# Formula Pattern — Fusion 360 add-in design

Date: 2026-11-06

## Intent

Fusion 360's native Rectangular and Circular Pattern commands only space
instances evenly. Many real parts need a *rule* for the spacing instead:
guitar fret slots (12-tone equal temperament), louvres or fins that get
denser toward one end, speaker ports that grow geometrically, decorative
rings whose angular step eases in and out. Today people compute those
numbers in a spreadsheet and place every instance by hand.

Formula Pattern adds one command, **Formula Pattern**, to the Solid → Create
panel. It looks and feels like the native pattern dialog, but the distance
(or angle) of each instance comes from a formula the user types or picks
from a preset.

## Goals

- Linear and circular patterns of faces, bodies, features or components,
  exactly the entity types the native pattern supports.
- A formula written in plain math (`a * (1 - 1 / 2^(i/12))`), with
  presets for the common cases so most users never write one.
- Immediately understandable: a live numeric preview of every instance's
  position, a live preview in the model, inline help for the formula.
- Parametric result: every instance distance ends up as an ordinary,
  editable model parameter in the timeline.

## Non-goals

- Two-direction (grid) patterns, symmetric patterns, path patterns.
- Editing an existing Formula Pattern through Fusion's own *Edit Feature*
  (that would need the Custom Feature API). Since 1.1.0 a saved pattern is
  edited instead by picking one of its copies in the dialog's *Saved
  pattern* field; the dialog reloads it from the attributes stored on the
  copies and OK replaces it in place.
- Patterning construction geometry.

## Anti-goals

- A formula box that silently produces garbage. Every error has to say
  *which instance* failed and *why*, before OK is enabled.
- A custom feature or a non-parametric base-feature result.

## How it is built (decisions made for this design)

**Result = one native pattern feature per instance, grouped.** For every
copy `k` the add-in creates a native Rectangular (or Circular) Pattern
feature with quantity 2 and extent = the formula's distance (or total
angle = the formula's angle). That gives instance 0 (the original) and one
copy at exactly the computed position. All features are collected into a
timeline group named "Formula Pattern" and each feature is named
`Formula Pattern 3/12`. This is why faces, bodies, features and
components all work, why cuts behave like cuts, and why every distance
stays an editable parameter.

**Sketch curves** use the same idea with the sketch API: one native
sketch pattern constraint (`GeometricConstraints.addRectangularPattern` /
`addCircularPattern`, quantity 2, extent = the computed distance or angle)
per copy, created in the sketch that owns the selected curves. The
direction is a sketch line (none → the sketch X axis); the centre is a
sketch point or a circle's centre. Two Fusion quirks found live: the
`setDirectionOne/Two` methods reject a null line although the docs
allow it, so quantities and distances are set through the input's
properties and a direction entity is only set when there is one; and
geometry selected inside a component occurrence arrives as a proxy that
the sketch API refuses, so entities are resolved to `nativeObject` first.
Sketch constraints are not timeline items, so there is no group. The
command sits in SKETCH → CREATE as well as SOLID → CREATE, and *Sketch
curves* is the default object type while a sketch is being edited.

**Formula semantics.** The formula is evaluated once per copy with:

| name | meaning |
|------|---------|
| `i`  | copy number, 1 … n (the original is 0 and is never recomputed) |
| `n`  | number of copies |
| `t`  | `i / n`, 0 → 1, handy for easing curves |
| `a`, `b`, `c` | tunable values from the dialog; `a` carries length (or angle) units |
| user parameters | any user parameter of the design by name, in document units |

Functions: `sin cos tan asin acos atan atan2` (radians), `sind cosd tand`
(degrees), `sqrt cbrt exp ln log log2 log10 abs floor ceil round trunc
min max pow clamp sign fib hypot rad deg`, constants `pi e tau phi`.
`^` means power. `x if cond else y` conditionals are allowed.

**Formula gives** either *Distance from original* (position mode, the
formula is the absolute offset of copy `i`) or *Gap to previous* (spacing
mode, positions are the running sum of the gaps). Circular patterns use
the same two modes in degrees. Angles are reduced modulo 360.

**Validation.** A copy that lands on the original (distance 0, or angle a
multiple of 360) is an error naming the copy. Two copies landing on the
same spot is a warning in the preview. Non-finite or non-real results,
unknown names, syntax errors and runaway exponents are errors.

**Presets** fill in formula, mode and a/b/c and explain the tunables:
Constant spacing, Guitar frets (12-TET, linear only), Growing gaps
(geometric), Growing gaps (arithmetic), Ease in/out (sine), Ease in
(power), Fibonacci gaps. Editing the formula switches the preset to
Custom.

**Dialog** (top to bottom): Pattern type · Object type (Bodies, Features,
Faces, Components, Sketch curves) · Objects · Direction/Axis · Flip ·
Copies · [Formula group: Preset, Formula gives, Formula, a, b, c, preset
notes] · [Preview: numeric list + total extent, "Show preview in model"
checkbox] · [Formula reference, collapsed] · [Advanced, collapsed: compute
option, timeline group name; hidden for sketch curves].

**Dialog lifecycle (a Fusion quirk).** Fusion fires `commandCreated`
before the dialog exists, and a *unitless* value input (`b`, `c`) has no
evaluated value until the dialog is on screen: `isValidExpression` is
False and reading or writing `expression`/`value` raises
`InternalValidationError`. Inputs with a unit type (`a`) work straight
away. So `commandCreated` only builds the inputs, restores the
non-numeric settings and attaches the handlers; restoring `a b c` and the
first preview happen in the `activate` event. Number reads tolerate a
not-yet-ready input by returning its creation default, and the preview
never raises, so the dialog always opens with its handlers attached.

**Preview.** `executePreview` builds the real features and marks the
result valid, so OK just keeps it. Model preview can be switched off and
is skipped above 100 copies; the numeric preview is always live.

**Persistence.** Last-used settings (not selections) are saved to
`~/.fusion_formula_pattern.json` and restored next time.

**Code layout.**

```
FormulaPattern/              the add-in (link or copy into Fusion's AddIns folder)
  FormulaPattern.manifest
  FormulaPattern.py          run()/stop() entry; drops cached submodules so Stop+Run reloads code
  command.py                 Fusion UI: dialog, events, feature / sketch-constraint creation
  engine.py                  pure Python: safe formula evaluator, presets, positions
  settings.py                load/save last-used settings
  resources/FormulaPattern/  16x16, 32x32, 64x64 icons
tests/test_engine.py         unittest suite for engine.py (no Fusion needed)
install.ps1 / install.sh     link the add-in into Fusion's AddIns folder
```

`engine.py` has no Fusion imports so it is fully unit-tested on any
Python. The evaluator is an AST whitelist (no attribute access, no
subscripts, no names outside the allowed set), evaluated in floats with
overflow and complex-result guards.

## Left to the builder

- Exact HTML of the preview and reference boxes.
- Icon artwork (dots with growing gaps).
- Position of the button inside the Create panels (after the native
  Rectangular Pattern control, ids `PatternRectangular` /
  `RectangularSketchPatternCommand` from Fusion's `TabToolbars.xml`, when
  found, else at the end).
