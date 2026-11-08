"""Dump the active design (timeline, bodies, occurrences, sketches) to ~/.fusion_fp_inspect.txt
so a live Formula Pattern test can be checked numerically. Lengths in mm, angles in degrees."""
import math
import os
import traceback
import adsk.core
import adsk.fusion


def mm(v):
    return round(v * 10.0, 3)


def rotation_about_z(m):
    # angle of the X axis image, degrees
    return round(math.degrees(math.atan2(m.getCell(1, 0), m.getCell(0, 0))), 2)


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent
        out = []
        tl = design.timeline
        out.append(f'TIMELINE marker {tl.markerPosition} of {tl.count}')
        for i in range(tl.count):
            item = tl.item(i)
            group = adsk.fusion.TimelineGroup.cast(item)
            if group is not None:
                names = []
                for j in range(group.count):
                    e = group.item(j).entity
                    try:
                        names.append(f'{e.objectType.split("::")[-1]}:{e.name}')
                    except Exception:
                        names.append(f'{type(e).__name__}:?')
                out.append(f'  {i}: GROUP {item.name} [{", ".join(names)}] rolledBack={item.isRolledBack}')
            else:
                e = item.entity
                try:
                    kind = e.objectType.split('::')[-1]
                except Exception:
                    kind = '?'
                out.append(f'  {i}: {kind} {item.name} rolledBack={item.isRolledBack}')
        out.append(f'BODIES {root.bRepBodies.count}')
        for b in root.bRepBodies:
            try:
                bb = b.boundingBox
                lo, hi = bb.minPoint, bb.maxPoint
                c = ((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, (lo.z + hi.z) / 2)
                vol = b.volume
                out.append(f'  {b.name}: visible={b.isVisible} centre=({mm(c[0])}, {mm(c[1])}, {mm(c[2])}) '
                           f'size=({mm(hi.x - lo.x)}, {mm(hi.y - lo.y)}, {mm(hi.z - lo.z)}) volume_mm3={round(vol * 1000, 2)}')
            except Exception as e:
                out.append(f'  {b.name}: ? {e}')
        out.append(f'OCCURRENCES {root.occurrences.count}')
        for o in root.occurrences:
            try:
                m = o.transform2
                t = m.translation
                cells = [round(m.getCell(r, c), 4) for r in range(3) for c in range(3)]
                out.append(f'  {o.name}: translation=({mm(t.x)}, {mm(t.y)}, {mm(t.z)}) rotZ={rotation_about_z(m)} '
                           f'rotation={cells}')
            except Exception as e:
                out.append(f'  {o.name}: ? {e}')
        out.append(f'SKETCHES {root.sketches.count}')
        for s in root.sketches:
            try:
                circles = [f'c(({mm(c.centerSketchPoint.geometry.x)}, {mm(c.centerSketchPoint.geometry.y)}) r{mm(c.radius)})'
                           for c in s.sketchCurves.sketchCircles]
                lines = s.sketchCurves.sketchLines.count
                pts = [f'p({mm(p.geometry.x)}, {mm(p.geometry.y)})' for p in s.sketchPoints if p.connectedEntities is None or p.connectedEntities.count == 0]
                out.append(f'  {s.name}: lines={lines} circles={len(circles)} points={s.sketchPoints.count} profiles={s.profiles.count}')
                for c in circles:
                    out.append(f'    {c}')
                for p in pts[:40]:
                    out.append(f'    {p}')
            except Exception as e:
                out.append(f'  {s.name}: ? {e}')
        path = os.path.join(os.path.expanduser('~'), '.fusion_fp_inspect.txt')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(out) + '\n')
    except Exception:
        if ui:
            ui.messageBox('FPInspect failed:\n{}'.format(traceback.format_exc()))
