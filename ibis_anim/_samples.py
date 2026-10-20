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
import functools
import string
from itertools import count

import ibis
import ibis.expr.operations as ops
import portion as P
import pyarrow as pa

from ibis_anim._derive import _frontier, _substitute
from ibis_anim._facts import ANY, TableFacts

ROWS, MAX_ROWS, SET_ROWS, POOL_ROWS = 6, 8, 4, 30  # POOL_ROWS: rows to choose from when the first rows reach no result
TESTS = {
    ops.Equals: lambda v: P.singleton(v), ops.NotEquals: lambda v: ANY - P.singleton(v),
    ops.Greater: lambda v: P.open(v, P.inf), ops.GreaterEqual: lambda v: P.closed(v, P.inf),
    ops.Less: lambda v: P.open(-P.inf, v), ops.LessEqual: lambda v: P.closed(-P.inf, v),
}
FLIP = {ops.Greater: ops.Less, ops.GreaterEqual: ops.LessEqual, ops.Less: ops.Greater, ops.LessEqual: ops.GreaterEqual}
NULL_STEPS = ("drop_null", "fill_null")


def needs_rows(table) -> bool:
    """True when the table reads a table with a schema only: its rows must be generated."""
    return _reads_unbound(table.op() if hasattr(table, "op") else table)


@functools.cache
def _reads_unbound(op) -> bool:
    return bool(op.find(ops.UnboundTable))  # ponytail: one walk of the plan for each op, then cached; a walk that stops at known ops is faster


def for_step(step, facts: dict, computed: dict | None = None, pool: bool = False) -> dict:
    """Generated rows (a pyarrow table) for each schema-only table that the step reads.

    The tables are the frontier of the step: usually its inputs, but a chained join reads the tables of
    the joins before it. A step on real data gets {}. A step that casts text gets tables without rows.
    `computed` maps a table op to the op of a table that holds its rows: a join key on real data reads it.
    With `pool`, the tables get POOL_ROWS different rows, with the literals of the step among the values: rows to
    choose from when the first rows reach no result.
    """
    output = step.output.op()
    # a named column of a table before the first input: the output reads the input, but the column table reads the earlier table
    columns_of = [t.op() for t in step.inputs[1:] if t.op() not in output.find(type(t.op()))]
    known = set(facts) | {t.op() for t in step.inputs} | {u for op in (output, *columns_of) for u in op.find(ops.UnboundTable)}
    tables = list(dict.fromkeys(op for root in (output, *columns_of) for op in _frontier(root, known) if needs_rows(op)))
    if not tables:
        return {}
    if _casts_text(output):
        return {op: _arrow(op, {c: [] for c in op.schema}) for op in tables}
    verb = step.op.split()[-1]
    n = POOL_ROWS if pool else SET_ROWS if verb in ("union", "intersect", "difference") else ROWS
    literals = [lit.value for v in _own_values(output) for lit in v.find(ops.Literal, filter=ops.Value)] if pool else []
    extra = [x for v in literals for x in ([v, v + "A", v + "B"] if isinstance(v, str) else [v])]  # 'VIP' for startswith('VIP')
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
        below = _below(op)
        m = 2 * n if any(kind == "test" for kind, *_ in below) else n  # a filter before the table drops rows: make more, keep n
        columns = {}
        for j, (name, dtype) in enumerate(op.schema.items()):
            col, unique = f.column(name), f.unique(name)
            period = 0 if unique or pool else repeat.get(name) or (3 if dtype.is_string() or dtype.is_boolean() else 0)
            values = _values(dtype, col.values, m, period, extra)
            mine = [(k, t) for k, t in enumerate(tests) if t[0] == (op, name)]
            if mine:
                chosen = []
                for r in range(m):
                    chosen.append(_tested(dtype, col, [(t[1], t[2], patterns[r % len(patterns)][k]) for k, t in mine], values[r], chosen))
                values = chosen
            if name in nulls and col.nulls != "never":
                values = [None if r % 3 == 1 + j % 2 else v for r, v in enumerate(values)]
            columns[name] = values
        data[op] = {c: v[:n] for c, v in _recomputed(op, columns, below).items()}
    for left, right in _join_keys(output, data):
        _link(data, left, right, facts, real, computed or {})
    if verb == "distinct" and not (facts.get(tables[0]) or TableFacts({})).keys:  # a declared key has no duplicate rows
        first = data[tables[0]]
        for name in first:
            first[name][-2:] = first[name][:2]  # two rows repeat
    if verb in ("union", "intersect", "difference") and len(tables) == 2:
        a, b = (data[t] for t in tables)
        for name in b:
            b[name][: n // 2] = a[name][1: 1 + n // 2]  # half of the rows are in both tables
    first = step.inputs[0]
    for op in {op for root in columns_of for op in _frontier(root, known)} & set(data) - {first.op()}:
        if first.op() in data and all(first.schema().get(c) == t for c, t in op.schema.items()):
            data[op] = {c: list(data[first.op()][c]) for c in op.schema}  # ibis reads the column on the rows of the input
    return {op: _arrow(op, columns) for op, columns in data.items()}


def _arrow(op, columns: dict) -> pa.Table:
    return pa.Table.from_pydict(columns, schema=ibis.schema(op.schema).to_pyarrow())


def _below(op) -> list:
    """The mutates and the filters before the table that read its own columns, the latest first:
    ("set", column, value) for a computed column, ("test", None, test) for a test of a filter.

    The search goes down through the steps that keep the columns of a row (filter, order_by, limit, mutate),
    and stops at a join or an aggregate. A cast of text is not computed again: generated text does not parse.
    """
    found, node = [], op
    while isinstance(node, (ops.Filter, ops.Sort, ops.Limit, ops.Project)):
        found += [("set", name, v) for name, v in getattr(node, "values", {}).items()  # ponytail: matched by name,
                  if name in op.schema and not isinstance(v, ops.Field) and _local(v, node, op)]  # a rename is not followed
        found += [("test", None, p) for p in getattr(node, "predicates", ()) if _local(p, node, op)]
        node = node.parent
    return found


def _local(value, node, op) -> bool:
    """True when `value` reads columns of the table below `node` that `op` also has, with no window, aggregate or cast."""
    return all(f.rel is node.parent and f.name in op.schema for f in value.find(ops.Field, filter=ops.Value)) and \
        not value.find((ops.WindowFunction, ops.Reduction, ops.Cast, ops.TryCast), filter=ops.Value)


def _recomputed(op, columns: dict, below: list) -> dict:
    """The columns, with each column that a mutate before the table computes from other columns of the table
    computed again from the generated rows, and without the rows that fail a filter before the table:
    after `big=_.amount > 200`, a row with amount 250 has big true; after `filter(_.big)`, no row has big false.

    Each value and test reads the generated columns, with the formula of each column that an earlier mutate
    computes in place of the column. Thus one flat query runs them all: a chain of mutates compiles slowly.
    """
    if not below:
        return columns
    t = ibis.memtable(_arrow(op, columns))
    values, tests = {}, []
    for kind, name, v in reversed(below):  # the earliest first: each one reads the columns as the steps before left them
        value = _substitute(v, {f: values.get(f.name, t[f.name].op()) for f in v.find(ops.Field, filter=ops.Value)})
        if kind == "set":  # ponytail: a formula that reads a column twice doubles in each later formula that reads it
            values[name] = value.to_expr().cast(op.schema[name]).op()
        else:
            tests.append(value.to_expr())
    final = {c: values[c].to_expr() if c in values else t[c] for c in op.schema}
    rows = (t.filter(*tests) if tests else t).select(**final).to_pyarrow()
    if not rows.num_rows:  # ponytail: no generated row passes the filters; the rows then break them
        rows = t.select(**final).to_pyarrow()
    return {c: rows[c].to_pylist() for c in op.schema}


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
    """((table, column), (table, column)) for each equality of each join of a join step, in the order of the joins.

    A chained join reads new rows for each of its tables, so each join of the chain needs keys that match.
    """
    if not isinstance(output, ops.JoinChain):
        return []
    tables, pairs = _all_tables(output), []
    for k, link in enumerate(output.rest):
        right = tables[k + 1]
        for p in link.predicates:
            if isinstance(p, ops.Equals) and isinstance(p.left, ops.Field) and isinstance(p.right, ops.Field):
                other, mine = (p.left, p.right) if _rel(p.right) is right else (p.right, p.left)
                left = _source(other, tables[: k + 1])
                if left is not None and _rel(mine) is right and right in data:
                    pairs.append((left, (right, mine.name)))
    return pairs


def _rel(field):
    return field.rel.parent if isinstance(field.rel, ops.JoinReference) else field.rel


def _all_tables(chain) -> list:
    return [getattr(t, "parent", t) for t in (chain.first, *(link.table for link in chain.rest))]


def _link(data: dict, left, right, facts: dict, real: dict, computed: dict):
    """The right key takes every second value of the left key: some rows match, the others do not."""
    table, name = left
    if table in data:
        source = data[table][name]
    else:  # a table with real data: its computed rows, when the scene has them
        if table not in real:
            real[table] = _substitute(table, computed).to_expr().head(ROWS).to_pyarrow()
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


def _values(dtype, allowed: P.Interval, n: int, period: int, extra=()) -> list:
    """`n` values of a type inside `allowed`; with `period`, the first `period` values repeat. `extra` values come first."""
    picked = _fitting(dtype, allowed, period or n, extra=extra) or _take(_candidates(dtype, ANY), period or n)  # else the type only
    return [picked[i % len(picked)] for i in range(n)]


def _fitting(dtype, allowed: P.Interval, n: int, avoid=(), extra=()) -> list:
    """Up to `n` different values of a type inside `allowed`, other than `avoid`. `extra` values are tried first."""
    picked = []
    for v in _take(_candidates(dtype, allowed, extra), 300):  # the plain values have no end
        if v not in picked and v not in avoid and _safe(lambda: _fits(dtype, v) and v in allowed, False):
            picked.append(v)
            if len(picked) == n:
                break
    return picked


def _take(values, n: int) -> list:
    return [v for _, v in zip(range(n), values)]


def _candidates(dtype, allowed: P.Interval, extra=()):
    """Values of a type to try: `extra`, the points and the bounds of `allowed`, values inside its bounded parts,
    then plain values of the type."""
    yield from extra
    points = [a.lower for a in allowed if not allowed.empty and a.lower == a.upper]
    yield from points
    bounds = [b for a in ([] if allowed.empty else allowed) for b in (a.lower, a.upper) if b not in (P.inf, -P.inf)]
    for b in bounds:
        yield from _around(dtype, b)
    for a in [] if allowed.empty else allowed:  # a narrow range, (100, 120): no value near its bounds is inside it
        yield from _inside(a.lower, a.upper)
    yield from _base(dtype)


def _inside(low, high) -> list:
    """The middle and the quarters of a bounded range, or nothing for a range without two bounds of one kind."""
    if P.inf in (low, high) or -P.inf in (low, high) or isinstance(low, bool) or isinstance(high, bool):
        return []
    if isinstance(low, int) and isinstance(high, int):
        return [low + (high - low) * k // 4 for k in (2, 1, 3)]
    try:
        return [low + (high - low) * k / 4 for k in (2, 1, 3)]  # float, decimal, date and timestamp
    except TypeError:  # text, or bounds of different kinds
        return []


def _around(dtype, b) -> list:
    """Values near a bound: on both sides of it, and the bound itself."""
    if isinstance(b, (datetime.date, datetime.datetime)):
        near = []
        for days in (1, 3, -1, -3):
            try:
                near.append(b + datetime.timedelta(days=days))
            except OverflowError:  # the bound is near the end of the range, for example 9999-12-31 for an open end
                pass
        return near + [b]
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
