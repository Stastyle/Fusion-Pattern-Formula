"""Formula Pattern — a Fusion 360 add-in.

Adds a "Formula Pattern" command to Solid > Create and Sketch > Create. It works
like the native Rectangular / Circular Pattern, except that a formula decides the
distance (or angle) of every copy. See README.md for details.
"""
import importlib
import sys
import traceback

import adsk.core

command = None   # the live FormulaPattern.command module, loaded by run()


def _package_name() -> str:
    """The package that ``from . import command`` would use for this file.

    Fusion runs an add-in's entry file as a module named after its folder, with
    ``__path__`` set and ``__package__`` left unset, so the import system resolves
    relative imports from ``__name__``. Mirror that resolution rather than trusting
    ``__package__``.
    """
    if __package__:
        return __package__
    spec = globals().get('__spec__')
    if spec is not None and getattr(spec, 'parent', ''):
        return spec.parent
    return __name__ if '__path__' in globals() else __name__.rpartition('.')[0]


def _forget_submodules():
    """Drop this add-in's modules from Python's cache.

    Fusion keeps one Python runtime for all add-ins and only re-reads this entry file
    when an add-in is run again, so without this a Stop + Run in the Scripts and
    Add-Ins dialog would keep executing the previously loaded command.py.
    """
    package = _package_name()
    if not package:
        return
    prefix = package + '.'
    for name in [m for m in sys.modules if m.startswith(prefix)]:
        del sys.modules[name]


def run(context):
    global command
    try:
        _forget_submodules()
        command = importlib.import_module('.command', _package_name())
        command.start()
    except Exception:
        ui = adsk.core.Application.get().userInterface
        ui.messageBox('Formula Pattern failed to start:\n' + traceback.format_exc())


def stop(context):
    try:
        if command is not None:
            command.stop()
    except Exception:
        ui = adsk.core.Application.get().userInterface
        ui.messageBox('Formula Pattern failed to stop cleanly:\n' + traceback.format_exc())
    finally:
        _forget_submodules()
