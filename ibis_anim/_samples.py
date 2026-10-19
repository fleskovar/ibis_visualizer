"""Sample rows for the steps that read tables with a schema only (`ibis.table(schema, name=...)`).

Each step gets its own rows. Rows made once for the source tables do not survive the filters and joins
of a long pipeline, so each step generates rows for the tables that it reads, from:

- the facts of each table: value sets, ranges, keys and nulls. The facts hold what the steps before the
  step guarantee, so the rows agree with them (after `status == "paid"`, each row has status "paid")
- the arguments of the step: each test of a filter (or of an `ifelse`) gets rows that pass it and rows that
  fail it, the keys of a join only partly match, group keys repeat, set operations share rows, and the
  columns that the step tests for null get nulls

A step that casts a text column to another type gets no rows: its video shows the change of the schema only.
The rows illustrate the step. They are not facts: the facts come from the code and the declarations only.
"""

from __future__ import annotations

import datetime
import decimal
import string
from itertools import count

import ibis
import ibis.expr.operations as ops
import portion as P
import pyarrow as pa

from ibis_anim._derive import _frontier
from ibis_anim._facts import ANY, TableFacts

ROWS, MAX_ROWS, SET_ROWS = 6, 8, 4
TESTS = {
    ops.Equals: lambda v: P.singleton(v), ops.NotEquals: lambda v: ANY - P.singleton(v),
    ops.Greater: lambda v: P.open(v, P.inf), ops.GreaterEqual: lambda v: P.closed(v, P.inf),
    ops.Less: lambda v: P.open(-P.inf, v), ops.LessEqual: lambda v: P.closed(-P.inf, v),
}
FLIP = {ops.Greater: ops.Less, ops.GreaterEqual: ops.LessEqual, ops.Less: ops.Greater, ops.LessEqual: ops.GreaterEqual}
NULL_STEPS = ("drop_null", "fill_null")


def needs_rows(table) -> bool:
    """True when the table reads a table with a schema only: its rows must be generated."""
    op = table.op() if hasattr(table, "op") else table
    return bool(op.find(ops.UnboundTable))


def for_step(step, facts: dict) -> dict:
    """Generated rows (a pyarrow table) for each schema-only table that the step reads.

    The tables are the frontier of the step: usually its inputs, but a chained join reads the tables of
    the joins before it. A step on real data gets {}. A step that casts text gets tables without rows.
    """
    output = step.output.op()
    known = set(facts) | {t.op() for t in step.inputs} | set(output.find(ops.UnboundTable))
    tables = [op for op in _frontier(output, known) if needs_rows(op)]
    if not tables:
        return {}
    if _casts_text(output):
        return {op: _arrow(op, {c: [] for c in op.schema}) for op in tables}
    verb = step.op.split()[-1]
    n = SET_ROWS if verb in ("union", "intersect", "difference") else ROWS
    tests = _tests(_own_values(output), tables)
    tests += [(key, P.singleton(True), False) for p in getattr(output, "predicates", ())  # filter(_.flag)
              if isinstance(p, ops.Field) and (key := _source(p, tables)) is not None]
    patterns = _patterns(len(tests))
    n = max(n, min(MAX_ROWS, len(patterns))) if tests else n
    repeat = _repeated(step, output)
    nulls = _null_columns(step, output)
    data, real = {}, {}
    for op in tables:
        f = facts.get(op) or TableFacts({})
        columns = {}
        for j, (name, dtype) in enumerate(op.schema.items()):
            col, unique = f.column(name), f.unique(name)
            period = 0 if unique else repeat.get(name) or (3 if dtype.is_string() or dtype.is_boolean() else 0)
            values = _values(dtype, col.values, n, period)
            mine = [(k, t) for k, t in enumerate(tests) if t[0] == (op, name)]
            if mine:
                chosen = []
                for r in range(n):
                    chosen.append(_tested(dtype, col, [(t[1], t[2], patterns[r % len(patterns)][k]) for k, t in mine], values[r], chosen))
                values = chosen
            if name in nulls and col.nulls != "never":
                values = [None if r % 3 == 1 + j % 2 else v for r, v in enumerate(values)]
            columns[name] = values
        data[op] = _recomputed(op, columns)
    for left, right in _join_keys(output, data):
        _link(data, left, right, facts, real)
    if verb == "distinct":
        first = data[tables[0]]
        for name in first:
            first[name][-2:] = first[name][:2]  # two rows repeat
    if verb in ("union", "intersect", "difference") and len(tables) == 2:
        a, b = (data[t] for t in tables)
        for name in b:
            b[name][: n // 2] = a[name][1: 1 + n // 2]  # half of the rows are in both tables
    return {op: _arrow(op, columns) for op, columns in data.items()}


def _arrow(op, columns: dict) -> pa.Table:
    return pa.Table.from_pydict(columns, schema=ibis.schema(op.schema).to_pyarrow())


def _recomputed(op, columns: dict) -> dict:
    """The columns, with each column that a mutate before the table computes from other columns of the table
    computed again from the generated rows: after `big=_.amount > 200`, a row with amount 250 has big true.

    The search goes down through the steps that keep the columns of a row (filter, order_by, limit, mutate),
    and stops at a join or an aggregate. A cast of text is not computed again: generated text does not parse.
    """
    found, node = {}, op
    while isinstance(node, (ops.Filter, ops.Sort, ops.Limit, ops.Project)):
        for name, v in getattr(node, "values", {}).items():
            fields = v.find(ops.Field, filter=ops.Value)
            if name in op.schema and name not in found and not isinstance(v, ops.Field) and \
                    all(f.rel is node.parent and f.name in op.schema for f in fields) and \
                    not v.find((ops.WindowFunction, ops.Reduction, ops.Cast, ops.TryCast), filter=ops.Value):
                found[name] = v  # ponytail: matched by name, so a column renamed after the mutate is not followed
        node = node.parent
    for name, v in reversed(found.items()):  # the earliest mutate first: a later one can read its columns
        mem = ibis.memtable(_arrow(op, columns))
        expr = v.replace({f.rel: mem.op() for f in v.find(ops.Field, filter=ops.Value)}).to_expr()
        columns[name] = mem.select(value=expr.cast(op.schema[name])).to_pyarrow()["value"].to_pylist()
    return columns


# -- what the step does ------------------------------------------------------------------------------


def _own_values(op) -> list:
    """The value expressions of one step (its filter, its new columns), without its input tables."""
    return [v for arg in op.__args__ for v in (arg if isinstance(arg, tuple) else (arg,)) if isinstance(v, ops.Value)] + \
        [v for v in getattr(op, "values", {}).values()] + [v for v in getattr(op, "metrics", {}).values()]


def _casts_text(op) -> bool:
    """True when the step casts a text column to another type: a generated text would not parse."""
    return any(c.arg.dtype.is_string() and not c.to.is_string()
               for v in _own_values(op) for c in v.find((ops.Cast, ops.TryCast), filter=ops.Value))


def _source(field, tables: list):
    """(table, column) of a field: through a join reference; else the first table with a column of that name."""
    rel = field.rel.parent if isinstance(field.rel, ops.JoinReference) else field.rel
    if rel in tables:
        return rel, field.name
    found = next((t for t in tables if field.name in t.schema), None)  # ponytail: by name only, when the field reads a table in between
    return (found, field.name) if found is not None else None


def _tests(values: list, tables: list) -> list:
    """Each test of a column in the step: ((table, column), values that pass, whether null passes)."""
    found = []
    for node in dict.fromkeys(n for v in values for n in v.find((*TESTS, ops.InValues, ops.Between, ops.IsNull, ops.NotNull), filter=ops.Value)):
        test = None
        if type(node) in TESTS:
            kind, field, literal = type(node), node.left, node.right
            if isinstance(literal, ops.Field) and isinstance(field, ops.Literal):
                kind, field, literal = FLIP.get(kind, kind), literal, field
            if isinstance(field, ops.Field) and isinstance(literal, ops.Literal):
                test = field, TESTS[kind](literal.value), False
        elif isinstance(node, ops.InValues) and isinstance(node.value, ops.Field) and all(isinstance(o, ops.Literal) for o in node.options):
            test = node.value, P.Interval(*(P.singleton(o.value) for o in node.options)), False
        elif isinstance(node, ops.Between) and isinstance(node.arg, ops.Field) and \
                isinstance(node.lower_bound, ops.Literal) and isinstance(node.upper_bound, ops.Literal):
            test = node.arg, P.closed(node.lower_bound.value, node.upper_bound.value), False
        elif isinstance(node, (ops.IsNull, ops.NotNull)) and isinstance(node.arg, ops.Field):
            test = node.arg, (P.empty() if isinstance(node, ops.IsNull) else ANY), isinstance(node, ops.IsNull)
        key = _source(test[0], tables) if test else None
        if key is not None:
            found.append((key, test[1], test[2]))
    return found


def _patterns(tests: int) -> list[list[bool]]:
    """Which tests each row passes: two rows pass all, one row fails each test on its own, one row fails all."""
    every = [True] * tests
    return [every, every, *([k != j for k in range(tests)] for j in range(tests)), [False] * tests]


def _repeated(step, output) -> dict:
    """Columns whose values repeat, so that groups have several rows: column → period."""
    keys = list(step.group_by)
    for w in (w for v in _own_values(output) for w in v.find(ops.WindowFunction, filter=ops.Value)):
        keys += [g.name for g in w.group_by if isinstance(g, ops.Field)]
    names_from = step.kwargs.get("names_from") if step.op.split()[-1] == "pivot_wider" else None
    keys += [names_from] if isinstance(names_from, str) else []
    return {k: 3 if i == 0 else 2 for i, k in enumerate(dict.fromkeys(keys))}


def _null_columns(step, output) -> set:
    """Columns that the step treats as nullable: `drop_null`, `fill_null`, `coalesce` and `fill_null` of a column."""
    found = {f.name for v in _own_values(output) for c in v.find(ops.Coalesce, filter=ops.Value)
             for f in c.find(ops.Field, filter=ops.Value)}
    if step.op.split()[-1] in NULL_STEPS:
        found |= set(step.used) or {c for t in step.inputs for c in t.columns}
    return found


def _join_keys(output, data: dict) -> list:
    """((table, column), (table, column)) for each equality of the last join of a join step."""
    if not isinstance(output, ops.JoinChain) or not output.rest:
        return []
    tables, pairs = list(data), []
    for p in output.rest[-1].predicates:
        if isinstance(p, ops.Equals) and isinstance(p.left, ops.Field) and isinstance(p.right, ops.Field):
            right = p.right.rel.parent if isinstance(p.right.rel, ops.JoinReference) else p.right.rel
            left = _source(p.left, [t for t in _all_tables(output) if t is not right])
            if left is not None and right in data:
                pairs.append((left, (right, p.right.name)))
    return pairs


def _all_tables(chain) -> list:
    return [getattr(t, "parent", t) for t in (chain.first, *(link.table for link in chain.rest))]


def _link(data: dict, left, right, facts: dict, real: dict):
    """The right key takes every second value of the left key: some rows match, the others do not."""
    table, name = left
    if table in data:
        source = data[table][name]
    else:  # a table with real data
        if table not in real:
            real[table] = table.to_expr().head(ROWS).to_pyarrow()
        source = real[table][name].to_pylist()
    values = data[right[0]][right[1]]
    f = facts.get(right[0]) or TableFacts({})
    dtype = right[0].schema[right[1]]
    shared = list(dict.fromkeys(v for v in source if v is not None))
    if f.unique(right[1]):
        shared = shared[::2]
    elif shared:  # a right key that can repeat: the first value matches twice, so that the join fans out
        shared = [shared[(r // 4) % len(shared)] for r in range(0, len(values), 2)]
    fresh = [v for v in _fitting(dtype, f.column(right[1]).values, len(values) * 2) if v not in source]
    for r in range(len(values)):
        values[r] = shared[r // 2] if r % 2 == 0 and r // 2 < len(shared) else fresh[r] if r < len(fresh) else values[r]


# -- values ------------------------------------------------------------------------------------------


def _tested(dtype, col, tests: list, default, earlier: list):
    """A value for one row: inside the facts of the column, and on the side of each test that the row needs."""
    allowed, null_ok = col.values, col.nulls != "never"
    for passes, nulls, wanted in tests:
        allowed = _safe(lambda: allowed & (passes if wanted else ANY - passes), allowed)
        null_ok = null_ok and nulls == wanted
    found = [] if allowed.empty else _fitting(dtype, allowed, 1, earlier) or _fitting(dtype, allowed, 1)
    if found:
        return found[0]
    return None if null_ok else default  # ponytail: tests that no value of the facts satisfies keep the plain value


def _values(dtype, allowed: P.Interval, n: int, period: int) -> list:
    """`n` values of a type inside `allowed`; with `period`, the first `period` values repeat."""
    picked = _fitting(dtype, allowed, period or n) or _take(_candidates(dtype, ANY), period or n)  # else the type only
    return [picked[i % len(picked)] for i in range(n)]


def _fitting(dtype, allowed: P.Interval, n: int, avoid=()) -> list:
    """Up to `n` different values of a type inside `allowed`, other than `avoid`."""
    picked = []
    for v in _take(_candidates(dtype, allowed), 300):  # the plain values have no end
        if v not in picked and v not in avoid and _safe(lambda: _fits(dtype, v) and v in allowed, False):
            picked.append(v)
            if len(picked) == n:
                break
    return picked


def _take(values, n: int) -> list:
    return [v for _, v in zip(range(n), values)]


def _candidates(dtype, allowed: P.Interval):
    """Values of a type to try: the points and the bounds of `allowed` first, then plain values of the type."""
    points = [a.lower for a in allowed if not allowed.empty and a.lower == a.upper]
    yield from points
    bounds = [b for a in ([] if allowed.empty else allowed) for b in (a.lower, a.upper) if b not in (P.inf, -P.inf)]
    for b in bounds:
        yield from _around(dtype, b)
    yield from _base(dtype)


def _around(dtype, b) -> list:
    """Values near a bound: on both sides of it, and the bound itself."""
    if isinstance(b, (datetime.date, datetime.datetime)):
        day = datetime.timedelta(days=1)
        return [b + day, b + 3 * day, b - day, b - 3 * day, b]
    if isinstance(b, bool) or not isinstance(b, (int, float, decimal.Decimal)):
        return [b]
    step = max(abs(b) * 0.5, 1)
    step = int(step) if isinstance(b, int) else step
    near = [b + step, b + 2 * step, b - step, b - 2 * step, b, b + 3 * step]
    if dtype.is_integer():
        near = [int(round(v)) for v in near]
    elif dtype.is_decimal():
        near = [decimal.Decimal(f"{v:.2f}") for v in near]
    return near


def _base(dtype):
    """Plain values of a type, without end: A B C ..., 1 2 3 ..., dates from 2024-01-01."""
    spread = [15.0, 120.0, 35.5, 250.0, 80.0, 310.0, 42.0, 199.9]
    for i in count():
        if dtype.is_string():
            yield string.ascii_uppercase[i % 26] * (1 + i // 26)
        elif dtype.is_integer():
            yield i + 1
        elif dtype.is_decimal():
            yield decimal.Decimal(f"{(i + 1) * 12.5:.2f}")
        elif dtype.is_floating():
            yield spread[i % len(spread)] + 1000 * (i // len(spread))
        elif dtype.is_boolean():
            if i > 1:
                return
            yield i == 0
        elif dtype.is_timestamp():
            yield datetime.datetime(2024, 1, 1, 9) + datetime.timedelta(days=i, hours=i)
        elif dtype.is_date():
            yield datetime.date(2024, 1, 1) + datetime.timedelta(days=i)
        elif dtype.is_array():
            yield _take(_base(dtype.value_type), i % 4)
        else:
            yield None
            return


def _fits(dtype, value) -> bool:
    if dtype.is_string():
        return isinstance(value, str)
    if dtype.is_boolean():
        return isinstance(value, bool)
    if dtype.is_integer():
        return isinstance(value, int) and not isinstance(value, bool)
    if dtype.is_floating():
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if dtype.is_decimal():
        return isinstance(value, (int, decimal.Decimal)) and not isinstance(value, bool)
    if dtype.is_timestamp():
        return isinstance(value, datetime.datetime)
    if dtype.is_date():
        return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)
    return True


def _safe(operation, default):
    """Compare values of different types (a date with a text) as "does not fit"."""
    try:
        return operation()
    except (TypeError, ValueError, decimal.InvalidOperation):
        return default
