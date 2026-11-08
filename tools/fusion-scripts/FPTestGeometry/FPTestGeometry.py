"""Test geometry for Formula Pattern 1.2 (run from Scripts and Add-Ins).

Everything in millimetres (the API wants cm).
  Block   120 x 80 x 10 at x 20..140, y 20..100, with sketch "Wells" on its top face holding
          a 10 x 10 rectangle at x 30..40, y 30..40  -> Profiles / Depth / Cut, Join, New body
  Peg     cylinder r 3, h 8 at (-60, 60)             -> Path pattern of a body, Size (scale)
  Louvre  plate 2 x 20 x 15 standing on the origin   -> Linear + Rotation about the Z axis
  Holes   sketch on XY with a circle r 2 at (-60, -40) -> sketch circles + Diameter
  PegComp component holding a cylinder r 4, h 10 at (-60, 110) -> Path pattern of a component
  Tilted  construction plane 30 deg about the X axis with sketch "Tilted": a circle r 4,
          a 10 x 4 rectangle and a sketch point -> sketch copies on a non-XY sketch
"""
import traceback
import adsk.core
import adsk.fusion


def mm(v):
    return v / 10.0


def rect(sketch, x0, y0, x1, y1):
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(
        adsk.core.Point3D.create(mm(x0), mm(y0), 0), adsk.core.Point3D.create(mm(x1), mm(y1), 0))


def profile_near(sketch, x, y):
    target = adsk.core.Point3D.create(mm(x), mm(y), 0)
    best, best_d = None, None
    for p in sketch.profiles:
        c = p.areaProperties().centroid
        d = c.distanceTo(target)
        if best is None or d < best_d:
            best, best_d = p, d
    return best


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent
        xy = root.xYConstructionPlane
        extrudes = root.features.extrudeFeatures
        new_body = adsk.fusion.FeatureOperations.NewBodyFeatureOperation

        # Block
        s = root.sketches.add(xy)
        s.name = 'Block'
        rect(s, 20, 20, 140, 100)
        block = extrudes.addSimple(profile_near(s, 80, 60), adsk.core.ValueInput.createByString('10 mm'), new_body)
        block.bodies.item(0).name = 'Block'
        top = block.endFaces.item(0)
        wells = root.sketches.add(top)
        wells.name = 'Wells'
        # sketch on the top face: sketch coordinates differ from model, so project through the sketch
        p0 = wells.modelToSketchSpace(adsk.core.Point3D.create(mm(30), mm(30), mm(10)))
        p1 = wells.modelToSketchSpace(adsk.core.Point3D.create(mm(40), mm(40), mm(10)))
        wells.sketchCurves.sketchLines.addTwoPointRectangle(p0, p1)

        # Peg
        s = root.sketches.add(xy)
        s.name = 'Peg'
        s.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(mm(-60), mm(60), 0), mm(3))
        peg = extrudes.addSimple(profile_near(s, -60, 60), adsk.core.ValueInput.createByString('8 mm'), new_body)
        peg.bodies.item(0).name = 'Peg'

        # Louvre on the origin
        s = root.sketches.add(xy)
        s.name = 'Louvre'
        rect(s, -1, -10, 1, 10)
        louvre = extrudes.addSimple(profile_near(s, 0, 0), adsk.core.ValueInput.createByString('15 mm'), new_body)
        louvre.bodies.item(0).name = 'Louvre'

        # Holes sketch (left open for the sketch tests)
        s = root.sketches.add(xy)
        s.name = 'Holes'
        s.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(mm(-60), mm(-40), 0), mm(2))

        # PegComp: a component with its own cylinder, for component patterns
        occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        comp = occ.component
        comp.name = 'PegComp'
        s = comp.sketches.add(comp.xYConstructionPlane)
        s.name = 'PegComp'
        s.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(mm(-60), mm(110), 0), mm(4))
        comp_peg = comp.features.extrudeFeatures.addSimple(
            profile_near(s, -60, 110), adsk.core.ValueInput.createByString('10 mm'), new_body)
        comp_peg.bodies.item(0).name = 'CompPeg'

        # Tilted plane (30 deg about the X axis) with a sketch for the non-XY sketch tests
        planes = root.constructionPlanes
        inp = planes.createInput()
        inp.setByAngle(root.xConstructionAxis, adsk.core.ValueInput.createByString('30 deg'), xy)
        plane = planes.add(inp)
        plane.name = 'Tilted'
        t = root.sketches.add(plane)
        t.name = 'Tilted'
        t.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(mm(-120), mm(-100), 0), mm(4))
        rect(t, -125, -85, -115, -81)
        t.sketchPoints.add(adsk.core.Point3D.create(mm(-120), mm(-70), 0))

        app.activeViewport.fit()
        ui.messageBox('Test geometry ready: Block (+ Wells sketch), Peg, Louvre, Holes sketch, '
                      'PegComp component, Tilted plane + sketch.')
    except Exception:
        if ui:
            ui.messageBox('FPTestGeometry failed:\n{}'.format(traceback.format_exc()))
