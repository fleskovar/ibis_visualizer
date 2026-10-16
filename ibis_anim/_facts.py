"""The abstract domain: what is known about the values of each column of a table.

The facts over-approximate: the real values of a column are always inside `values`. A fact that is
not certain is not stated. Value sets are `portion` intervals, so one form covers finite sets
(`{'paid', 'shipped'}`), ranges (`[0, +inf)`) and complements (`anything except 'trial'`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import portion as P

ANY = P.open(-P.inf, P.inf)
NEVER, MAYBE, ALWAYS = "never", "maybe", "always"  # can the column be null?


@dataclass(frozen=True)
class Column:
    values: P.Interval = ANY
    nulls: str = MAYBE
    equal_to: frozenset = frozenset()  # source columns with exactly the same set of values, as "table.column"
    subset_of: frozenset = frozenset()  # source columns whose set of values contains this one
    fields: tuple = ()  # for a struct column: (field name, Column) pairs

    def narrowed(self) -> Column:
        """The column after rows were removed: equal sets become subsets."""
        return replace(self, equal_to=frozenset(), subset_of=self.subset_of | self.equal_to)


@dataclass
class TableFacts:
    columns: dict[str, Column]
    keys: list[frozenset] = field(default_factory=list)  # column sets that identify a row
    rows: str = ""  # row count compared with the input, for example "≤ input"
    warnings: list[str] = field(default_factory=list)
    ranks: dict = field(default_factory=dict)  # origin "table.column" → step where it began; source tables are -1
    can_empty: bool = False  # the step can return no row even when its inputs have rows (a filter, an inner join)

    def column(self, name: str) -> Column:
        return self.columns.get(name, Column())

    def unique(self, name: str) -> bool:
        return frozenset({name}) in self.keys


# -- declarations on source tables ----------------------------------------------------------------

_declared: dict = {}  # table op → (keys, values, not_null, references)


def declare(table, key=None, values=None, not_null=(), references=None):
    """State what is known about a source table, for example:

    declare(orders, key="order_id", values={"status": {"paid", "refunded"}, "amount": (0, None)},
            not_null=["order_id"], references={"customer_id": customers.customer_id})

    `values` maps a column to a set of values or to a (low, high) range, where None is unbounded.
    Declare only constraints that hold for any data (an enum type, a CHECK constraint, a business
    rule), never values read from a sample: the facts must not depend on the data.
    """
    keys = [frozenset([key] if isinstance(key, str) else key)] if key else []
    sets = {name: _as_values(spec) for name, spec in (values or {}).items()}
    refs = {name: (column.op().rel, column.get_name()) for name, column in (references or {}).items()}
    _declared[table.op()] = (keys, sets, set(not_null) | {c for k in keys for c in k}, refs)
    return table


def _as_values(spec) -> P.Interval:
    if isinstance(spec, tuple):
        low, high = spec
        return P.closed(-P.inf if low is None else low, P.inf if high is None else high)
    return P.Interval(*(P.singleton(v) for v in spec))


def source_facts(table, name: str, names: dict) -> TableFacts:
    """Facts of a table that no traced step made: its declarations and the nullability of its schema."""
    keys, sets, not_null, refs = _declared.get(table.op(), ([], {}, set(), {}))
    columns = {}
    for col, dtype in table.schema().items():
        ref = refs.get(col)
        columns[col] = Column(
            values=sets.get(col, ANY),
            nulls=NEVER if col in not_null or not dtype.nullable else MAYBE,
            equal_to=frozenset({f"{name}.{col}"}),
            subset_of=frozenset({f"{names.get(ref[0], 'table')}.{ref[1]}"}) if ref else frozenset(),
        )
    return TableFacts(columns, list(keys))


# -- set helpers ------------------------------------------------------------------------------------


def safe(operation, default=ANY):
    """Run a set operation; values of types that do not compare give the sound answer `default`."""
    try:
        return operation()
    except (TypeError, ValueError):
        return default


def hull(values: P.Interval) -> P.Interval:
    return ANY if values.empty else P.closed(values.lower, values.upper)


def _float(bound) -> float:
    return math.inf if bound == P.inf else -math.inf if bound == -P.inf else bound


def _bound(x: float):
    return P.inf if x == math.inf else -P.inf if x == -math.inf else x


def arithmetic(op: str, a: P.Interval, b: P.Interval) -> P.Interval:
    """Range arithmetic for + - * on numeric hulls. Anything else gives ANY."""
    if a.empty or b.empty or op not in "+-*":
        return ANY
    try:
        lo1, hi1, lo2, hi2 = (_float(x) for x in (a.lower, a.upper, b.lower, b.upper))
        if op == "+":
            ends = [lo1 + lo2, hi1 + hi2]
        elif op == "-":
            ends = [lo1 - hi2, hi1 - lo2]
        else:  # at the bounds, 0 × ∞ is 0
            ends = [0.0 if 0 in (x, y) else x * y for x in (lo1, hi1) for y in (lo2, hi2)]
    except TypeError:
        return ANY
    if any(math.isnan(e) for e in ends):
        return ANY
    return P.closed(_bound(min(ends)), _bound(max(ends)))


def describe(values: P.Interval, limit: int = 4) -> str:
    """A short text for a value set: '{paid, shipped}', '≥ 0', '≠ trial', '[1, 5]', or '' for anything."""
    if values == ANY:
        return ""
    if values.empty:
        return "∅ (no value)"

    def text(v):
        return f"{v:g}" if isinstance(v, float) else str(v)

    def points(iv):
        return [a.lower for a in iv] if all(a.lower == a.upper and a.left == P.CLOSED for a in iv) else None

    finite = points(values)
    if finite is not None:
        shown = ", ".join(map(text, finite[:limit])) + (", …" if len(finite) > limit else "")
        return f"{{{shown}}}"
    excluded = safe(lambda: points(ANY - values), None)
    if excluded:
        return f"≠ {text(excluded[0])}" if len(excluded) == 1 else f"∉ {{{', '.join(map(text, excluded[:limit]))}}}"
    parts = []
    for atom in values:
        lo, hi = atom.lower, atom.upper
        if lo == hi:
            parts.append(f"{{{text(lo)}}}")
        elif lo == -P.inf:
            parts.append(f"{'≤' if atom.right == P.CLOSED else '<'} {text(hi)}")
        elif hi == P.inf:
            parts.append(f"{'≥' if atom.left == P.CLOSED else '>'} {text(lo)}")
        else:
            parts.append(f"{'[' if atom.left == P.CLOSED else '('}{text(lo)}, {text(hi)}{']' if atom.right == P.CLOSED else ')'}")
    return " ∪ ".join(parts)
