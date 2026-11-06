"""A small stand-in for Fusion 360's ``adsk`` package.

Just enough behaviour to drive FormulaPattern/command.py outside Fusion:
command inputs that remember their values, drop-downs with selection,
events that can be fired, pattern feature collections that record what
was created, and a timeline with a marker. Test-only code.
"""
from . import core, fusion  # noqa: F401
