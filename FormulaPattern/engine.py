"""Formula engine for the Formula Pattern add-in.

Pure Python with no Fusion 360 imports, so it can be unit-tested anywhere.
It knows how to:

* parse and safely evaluate a user formula (``Formula``),
* turn a formula into the offset of every copy (``compute_positions``),
* describe the built-in presets (``PRESETS``),
* render the numeric preview and the formula reference as simple HTML.
"""
import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

MODE_POSITION = 'position'   # formula = offset of copy i from the original
MODE_SPACING = 'spacing'     # formula = gap between copy i-1 and copy i

PHI = (1.0 + 5.0 ** 0.5) / 2.0

# A copy closer than this to the original (or to a 360° multiple) is treated as "on the original".
ZERO_TOLERANCE = 1e-7


class FormulaError(ValueError):
    """A problem with the formula text or with one of the computed copies."""


# ----------------------------------------------------------------------------
# Functions and constants available inside a formula
# ----------------------------------------------------------------------------

def _pow(base, exponent):
    base, exponent = float(base), float(exponent)
    if abs(base) > 1.0 and abs(exponent) > 10000:
        raise FormulaError('Exponent is far too large')
    try:
        result = base ** exponent
    except OverflowError:
        raise FormulaError('Number too large to represent') from None
    except ZeroDivisionError:
        raise FormulaError('Zero raised to a negative power') from None
    if isinstance(result, complex):
        raise FormulaError('A negative number raised to a fractional power has no real result')
    return result


def _fib(k):
    k = int(round(k))
    if k < 0:
        raise FormulaError('fib() needs a number of 0 or more')
    if k > 1400:
        raise FormulaError('fib() argument is too large')
    a, b = 0, 1
    for _ in range(k):
        a, b = b, a + b
    return float(a)


def _log(x, base=None):
    return math.log(x) if base is None else math.log(x, base)


def _cbrt(x):
    return math.copysign(abs(x) ** (1.0 / 3.0), x)


def _round(x, digits=0):
    return float(round(x, int(digits)))


def _clamp(x, lo, hi):
    return min(max(x, lo), hi)


def _sign(x):
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


FUNCTIONS = {
    # trigonometry in radians
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
    'asin': math.asin, 'acos': math.acos, 'atan': math.atan, 'atan2': math.atan2,
    'sinh': math.sinh, 'cosh': math.cosh, 'tanh': math.tanh,
    # trigonometry in degrees
    'sind': lambda d: math.sin(math.radians(d)),
    'cosd': lambda d: math.cos(math.radians(d)),
    'tand': lambda d: math.tan(math.radians(d)),
    'asind': lambda x: math.degrees(math.asin(x)),
    'acosd': lambda x: math.degrees(math.acos(x)),
    'atand': lambda x: math.degrees(math.atan(x)),
    'rad': math.radians, 'deg': math.degrees,
    # powers and logarithms
    'sqrt': math.sqrt, 'cbrt': _cbrt, 'exp': math.exp, 'pow': _pow,
    'ln': math.log, 'log': _log, 'log2': math.log2, 'log10': math.log10,
    # rounding and limits
    'abs': abs, 'floor': math.floor, 'ceil': math.ceil, 'round': _round, 'trunc': math.trunc,
    'min': min, 'max': max, 'clamp': _clamp, 'sign': _sign, 'hypot': math.hypot,
    # sequences
    'fib': _fib,
}

CONSTANTS = {'pi': math.pi, 'e': math.e, 'tau': math.tau, 'phi': PHI}

_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: _pow,
}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Not: operator.not_}
_COMPARE = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Name, ast.Constant, ast.Load,
    ast.And, ast.Or,
) + tuple(_BINARY) + tuple(_UNARY) + tuple(_COMPARE)


class Formula:
    """A parsed, validated formula that can be evaluated many times."""

    def __init__(self, text: str):
        self.text = (text or '').strip()
        if not self.text:
            raise FormulaError('The formula is empty')
        source = self.text.replace('^', '**')
        try:
            self._tree = ast.parse(source, mode='eval')
        except SyntaxError as e:
            raise FormulaError(f"Can't read the formula: {_describe_syntax_error(e)}") from None
        self.names: Set[str] = set()
        self._validate(self._tree)

    def _validate(self, tree):
        for node in ast.walk(tree):
            if not isinstance(node, _ALLOWED_NODES):
                raise FormulaError(_disallowed_message(node))
            if isinstance(node, ast.Constant):
                if isinstance(node.value, bool):
                    continue
                if not isinstance(node.value, (int, float)):
                    raise FormulaError('Only numbers are allowed in a formula')
            elif isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name):
                    raise FormulaError('Only named functions such as sin(x) can be called')
                if node.func.id not in FUNCTIONS:
                    raise FormulaError(f"Unknown function '{node.func.id}()'. "
                                       'See the Formula reference for the available functions')
                if node.keywords:
                    raise FormulaError('Functions take plain arguments, for example log(x, 2)')
        function_names = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in function_names and node.id not in CONSTANTS:
                self.names.add(node.id)

    def evaluate(self, variables: Dict[str, float]) -> float:
        try:
            value = self._eval(self._tree.body, variables)
        except FormulaError:
            raise
        except ZeroDivisionError:
            raise FormulaError('Division by zero') from None
        except OverflowError:
            raise FormulaError('Number too large to represent') from None
        except ValueError as e:
            raise FormulaError(f'Math error: {e}') from None
        if isinstance(value, bool):
            value = float(value)
        if isinstance(value, complex) or not isinstance(value, (int, float)):
            raise FormulaError('The formula does not give a real number')
        value = float(value)
        if not math.isfinite(value):
            raise FormulaError('The result is infinite or not a number')
        return value

    def _eval(self, node, env):
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, bool) else float(node.value)
        if isinstance(node, ast.Name):
            if node.id in env:
                return float(env[node.id])
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
            raise FormulaError(f"Unknown name '{node.id}'. You can use a, b, c, i, n, t, "
                               'a user parameter of the design, or a function from the Formula reference')
        if isinstance(node, ast.BinOp):
            return _BINARY[type(node.op)](self._eval(node.left, env), self._eval(node.right, env))
        if isinstance(node, ast.UnaryOp):
            return _UNARY[type(node.op)](self._eval(node.operand, env))
        if isinstance(node, ast.BoolOp):
            values = [self._eval(v, env) for v in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, env)
            for op, comparator in zip(node.ops, node.comparators):
                right = self._eval(comparator, env)
                if not _COMPARE[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.IfExp):
            return self._eval(node.body if self._eval(node.test, env) else node.orelse, env)
        if isinstance(node, ast.Call):
            args = [self._eval(a, env) for a in node.args]
            try:
                return FUNCTIONS[node.func.id](*args)
            except TypeError:
                raise FormulaError(f'Wrong number of arguments for {node.func.id}()') from None
        raise FormulaError('Unsupported expression')


def _describe_syntax_error(e: SyntaxError) -> str:
    msg = (e.msg or 'syntax error').replace('invalid syntax', 'unexpected or missing symbol')
    if e.offset:
        return f'{msg} near position {e.offset}'
    return msg


def _disallowed_message(node) -> str:
    if isinstance(node, ast.Attribute):
        return 'Dots are not allowed in a formula (use a plain name like a or sin(x))'
    if isinstance(node, (ast.Subscript, ast.List, ast.Tuple, ast.Dict, ast.Set)):
        return 'Brackets and lists are not allowed in a formula'
    if isinstance(node, ast.Lambda):
        return 'lambda is not allowed in a formula'
    if isinstance(node, ast.NamedExpr):
        return 'Assignments are not allowed in a formula'
    return f'{type(node).__name__} is not allowed in a formula'


# ----------------------------------------------------------------------------
# From a formula to the offset of every copy
# ----------------------------------------------------------------------------

@dataclass
class PatternSpec:
    formula: str
    count: int
    mode: str = MODE_POSITION
    circular: bool = False
    flip: bool = False
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    extra_vars: Optional[Dict[str, float]] = None  # user parameters of the design, by name


@dataclass
class PatternResult:
    values: List[float]            # raw formula result for copy 1..n
    positions: List[float]         # offset of copy 1..n from the original, ready to build
    extent: float                  # distance (or angle) of the farthest copy
    warnings: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.positions)


def compute_positions(spec: PatternSpec) -> PatternResult:
    """Evaluate the formula for every copy and return the offsets to build.

    Raises FormulaError with a message that names the failing copy.
    """
    n = int(spec.count)
    if n < 1:
        raise FormulaError('Copies must be at least 1')
    formula = Formula(spec.formula)

    env: Dict[str, float] = dict(spec.extra_vars or {})
    env.update({'a': float(spec.a), 'b': float(spec.b), 'c': float(spec.c), 'n': float(n)})

    values: List[float] = []
    positions: List[float] = []
    running = 0.0
    for i in range(1, n + 1):
        env['i'] = float(i)
        env['t'] = i / n
        try:
            v = formula.evaluate(env)
        except FormulaError as e:
            raise FormulaError(f'Copy i = {i}: {e}') from None
        values.append(v)
        if spec.mode == MODE_SPACING:
            running += v
            positions.append(running)
        else:
            positions.append(v)

    if spec.flip:
        positions = [-p for p in positions]
    if spec.circular:
        positions = [math.fmod(p, 360.0) for p in positions]

    for i, p in enumerate(positions, 1):
        if abs(p) < ZERO_TOLERANCE:
            why = 'its angle is a multiple of 360°' if spec.circular else 'its distance is 0'
            raise FormulaError(f'Copy {i} lands on the original ({why}). '
                               'Change the formula so every copy moves away from it')

    warnings = _duplicate_warnings(positions, spec.circular)
    extent = max(abs(p) for p in positions)
    return PatternResult(values=values, positions=positions, extent=extent, warnings=warnings)


def _duplicate_warnings(positions: List[float], circular: bool) -> List[str]:
    def key(p):
        return p % 360.0 if circular else p
    order = sorted(range(len(positions)), key=lambda k: key(positions[k]))
    pairs: List[Tuple[int, int]] = []
    for prev, cur in zip(order, order[1:]):
        if abs(key(positions[cur]) - key(positions[prev])) < 1e-6:
            pairs.append((min(prev, cur) + 1, max(prev, cur) + 1))
    if circular and len(order) > 1:
        first, last = order[0], order[-1]
        if abs(key(positions[last]) - key(positions[first]) - 360.0) < 1e-6:
            pairs.append((min(first, last) + 1, max(first, last) + 1))
    warnings = [f'Copies {a} and {b} land on the same spot' for a, b in pairs[:3]]
    if len(pairs) > 3:
        warnings.append(f'… and {len(pairs) - 3} more overlapping pairs')
    return warnings


# ----------------------------------------------------------------------------
# Presets
# ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    kinds: Tuple[str, ...]   # 'linear' and/or 'circular'
    mode: str
    formula: str
    a_mm: float              # default for a in a linear pattern (millimetres)
    a_deg: float             # default for a in a circular pattern (degrees)
    b: float
    c: float
    notes: str


BOTH = ('linear', 'circular')

PRESETS: List[Preset] = [
    Preset('custom', 'Custom', BOTH, MODE_POSITION, 'a * i', 10.0, 15.0, 0.0, 0.0,
           'Write any formula. i is the copy number (1 … n), t runs 0 → 1, and a, b, c are the values below.'),
    Preset('constant', 'Constant spacing', BOTH, MODE_SPACING, 'a', 10.0, 15.0, 0.0, 0.0,
           'a = gap between neighbours. The same as the native pattern; edit the formula from here.'),
    Preset('frets', 'Guitar frets (12-TET)', ('linear',), MODE_POSITION, 'a * (1 - 1 / 2^(i / 12))',
           648.0, 0.0, 0.0, 0.0,
           'a = scale length (648 mm ≈ 25.5 in). Put the original at the nut: copy i becomes fret i. '
           'Change the 12 for other temperaments.'),
    Preset('geometric', 'Growing gaps (geometric)', BOTH, MODE_SPACING, 'a * b^(i - 1)', 10.0, 10.0, 1.2, 0.0,
           'a = first gap, b = growth per step (1.2 = +20 % each time; use 0.8 to shrink).'),
    Preset('arithmetic', 'Growing gaps (arithmetic)', BOTH, MODE_SPACING, 'a + b * (i - 1)', 10.0, 10.0, 2.0, 0.0,
           'a = first gap, b = how much each gap grows, in document units (or degrees).'),
    Preset('ease_sine', 'Ease in & out (sine)', BOTH, MODE_POSITION, 'a * (1 - cos(pi * t)) / 2',
           100.0, 180.0, 0.0, 0.0,
           'a = total length (or angle) reached by the last copy. Copies bunch up at both ends.'),
    Preset('ease_power', 'Ease in (power)', BOTH, MODE_POSITION, 'a * t^b', 100.0, 180.0, 2.0, 0.0,
           'a = total length (or angle), b = curve: b > 1 bunches copies near the start, '
           'b < 1 near the end, 1 spaces them evenly.'),
    Preset('fibonacci', 'Fibonacci gaps', BOTH, MODE_SPACING, 'a * fib(i)', 5.0, 5.0, 0.0, 0.0,
           'a = size of the first gap; the gaps follow 1, 1, 2, 3, 5, 8, 13 …'),
    Preset('golden_angle', 'Golden angle (phyllotaxis)', ('circular',), MODE_SPACING, 'a', 0.0, 137.508, 0.0, 0.0,
           'a = the golden angle, 137.508°. Each copy turns by it, so copies never line up (sunflower seeds).'),
]


def presets_for(circular: bool) -> List[Preset]:
    kind = 'circular' if circular else 'linear'
    return [p for p in PRESETS if kind in p.kinds]


def preset_by_id(preset_id: str) -> Optional[Preset]:
    for p in PRESETS:
        if p.id == preset_id:
            return p
    return None


# ----------------------------------------------------------------------------
# Text for the dialog
# ----------------------------------------------------------------------------

def format_number(value: float) -> str:
    text = f'{value:.3f}'.rstrip('0').rstrip('.')
    return '0' if text in ('-0', '') else text


def preview_html(result: PatternResult, unit: str, circular: bool, mode: str,
                 flip: bool = False, max_lines: int = 12) -> str:
    """Numeric preview of the copies, as simple HTML for a read-only text box."""
    u = '°' if circular else f' {unit}'
    n = result.count
    what = 'largest angle' if circular else 'farthest copy'
    lines = [f'<b>{n} {"copy" if n == 1 else "copies"}</b> · {what} {format_number(result.extent)}{u}']

    def line(k):
        text = f'i = {k + 1}: {format_number(result.positions[k])}{u}'
        if mode == MODE_SPACING:
            gap = -result.values[k] if flip else result.values[k]
            text += f' ({"+" if gap >= 0 else ""}{format_number(gap)})'
        return text

    if n <= max_lines:
        lines.extend(line(k) for k in range(n))
    else:
        lines.extend(line(k) for k in range(max_lines - 2))
        lines.append('…')
        lines.append(line(n - 1))
    for w in result.warnings:
        lines.append(f'<i>⚠ {w}</i>')
    return '<br>'.join(lines)


def error_html(message: str) -> str:
    return f'<font color="#c0392b"><b>✖ {message}</b></font>'


REFERENCE_HTML = (
    '<b>Variables</b><br>'
    '<b>i</b> copy number 1 … n (the original is 0) · <b>n</b> number of copies · '
    '<b>t</b> = i / n, runs 0 → 1<br>'
    '<b>a</b>, <b>b</b>, <b>c</b> the values above · any <b>user parameter</b> of the design by name<br>'
    '<b>Operators</b> + − * / ^ (power) % · comparisons and <i>x if cond else y</i><br>'
    '<b>Functions</b> sin cos tan asin acos atan atan2 (radians) · sind cosd tand (degrees) · '
    'rad deg · sqrt cbrt exp pow · ln log(x, base) log2 log10 · '
    'abs floor ceil round trunc · min max clamp(x, lo, hi) sign hypot · fib(i)<br>'
    '<b>Constants</b> pi e tau phi (golden ratio)<br>'
    '<b>Examples</b> frets: <i>a * (1 - 1 / 2^(i/12))</i> · geometric gaps: <i>a * b^(i-1)</i> · '
    'bunch at the end: <i>a * t^0.5</i>'
)
