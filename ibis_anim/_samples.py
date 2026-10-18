"""Sample data for tables that have only a schema (`ibis.table(schema, name=...)`).

The video needs rows, but a pipeline can be written against schemas only. Then each source table gets
a few generated rows, from the type of each column and from the trace:

- keys get distinct values, other columns repeat values (so that joins can fan out and groups have rows)
- columns that a join compares get values that only partly match ("A, B, C" and "A, D, C"); a declared
  foreign key takes its values from the column that it references
- a column that the code compares with a literal (`status == "paid"`, `amount > 100`) gets that literal
  and values on the other side of the test, so that each condition has rows that pass and rows that fail
- declared value sets and ranges are kept

The rows illustrate the steps. They are not facts: the facts come from the declarations only.
"""

from __future__ import annotations

import datetime
import decimal
import string

import ibis
import ibis.expr.operations as ops
import pyarrow as pa

from ibis_anim._facts import ANY, _declared

ROWS = 6
COMPARISONS = (ops.Equals, ops.NotEquals, ops.Greater, ops.GreaterEqual, ops.Less, ops.LessEqual)


def sources(steps) -> list:
    """The schema-only tables that the trace reads, in order of first use."""
    found = {}
    for step in steps:
        for table in step.inputs:
            for node in table.op().find(ops.UnboundTable):
                found.setdefault(node, None)
    return list(found)


def generate(steps, names: dict, facts: dict) -> dict:
    """Generated rows (a pyarrow table) for each schema-only source table of the trace."""
    tables = sources(steps)
    if not tables:
        return {}
    by_name = {names.get(op, getattr(op, "name", "")): op for op in tables} | {op.name: op for op in tables}
    origin = lambda text: (by_name.get(text.rsplit(".", 1)[0]), text.rsplit(".", 1)[1]) if "." in text else (None, None)  # noqa: E731

    def source_columns(table_op, column, depth: int = 0) -> set:
        """The source columns whose values a column of a traced table carries: from the lineage of the facts,
        and through projections of the column or of a cast of it (`store_id.cast("int")` keeps its values)."""
        if table_op in tables:
            return {(table_op, column)}
        col = facts.get(table_op).column(column) if table_op in facts else None
        found = {origin(o) for o in (col.equal_to | col.subset_of)} if col else set()
        value = getattr(table_op, "values", {}).get(column)
        value = value.arg if isinstance(value, ops.Cast) else value
        if isinstance(value, ops.Field) and value.rel is not table_op and depth < 30:
            found |= source_columns(value.rel, value.name, depth + 1)
        return {(t, c) for t, c in found if t is not None}

    hints, links, numeric_text = {}, [], set()
    for step in steps:
        for node in _own_values(step.output.op()):
            for cast in node.find(ops.Cast, filter=ops.Value):  # text that the code casts to a number must look like one
                if isinstance(cast.arg, ops.Field) and (cast.to.is_integer() or cast.to.is_floating() or cast.to.is_decimal()):
                    numeric_text |= source_columns(cast.arg.rel, cast.arg.name)
            for cmp in node.find(COMPARISONS, filter=ops.Value):
                field, literal = _field_and_literal(cmp)
                if field is not None:
                    for key in source_columns(field.rel, field.name):
                        hints.setdefault(key, []).append((type(cmp), literal.value))
            for inv in node.find(ops.InValues, filter=ops.Value):
                if isinstance(inv.value, ops.Field) and all(isinstance(o, ops.Literal) for o in inv.options):
                    for key in source_columns(inv.value.rel, inv.value.name):
                        hints.setdefault(key, []).extend((ops.Equals, o.value) for o in inv.options)
        if step.op.split()[-1] == "join" and len(step.inputs) > 1:
            left, right = step.inputs[0], step.inputs[1]
            on_left, on_right = [c for c in step.used if c in left.columns], [c for c in step.used if c in right.columns]
            pairs = list(zip(on_left, on_right)) if len(on_left) == len(on_right) == 1 else [(c, c) for c in on_left if c in on_right]
            for a, b in pairs:  # the two columns that the join compares
                linked = source_columns(left.op(), a) | source_columns(right.op(), b)
                if len(linked) > 1:
                    links.append(linked)
    groups = _merge(links)
    group_of = {col: g for g in groups for col in g}
    data, done = {}, {}
    for table in sorted(tables, key=lambda t: _depends_on(t) is not None):  # referenced tables first
        keys, values, not_null, refs = _declared.get(table, ([], {}, set(), {}))
        unique = {next(iter(k)) for k in keys if len(k) == 1}
        columns = {}
        for name, dtype in table.schema.items():
            ref = refs.get(name)
            as_text = dtype.is_string() and (table, name) in numeric_text
            dtype = ibis.dtype("int64") if as_text else dtype  # generate numbers, then write them as text
            if ref is not None and (ref[0], ref[1]) in done:  # a foreign key takes the values that it references
                parent = done[ref[0], ref[1]]
                columns[name] = [parent[(i * 2) % len(parent)] for i in range(ROWS)]
            elif (table, name) in group_of:
                group = sorted(group_of[table, name], key=lambda c: (c[0].name, c[1]))
                columns[name] = _linked(dtype, group.index((table, name)), name in unique)
            else:
                columns[name] = _column(dtype, name in unique, values.get(name), hints.get((table, name), []))
            if as_text:
                columns[name] = [None if v is None else str(v) for v in columns[name]]
            done[table, name] = columns[name]
        data[table] = pa.Table.from_pydict(columns, schema=ibis.schema(table.schema).to_pyarrow())
    return data


def bind(data: dict):
    """A function that runs a table on the generated rows instead of on its schema-only sources."""
    if not data:
        return lambda table: table
    mapping = {op: ibis.memtable(rows).cast(op.schema).op() for op, rows in data.items()}
    return lambda table: table.op().replace(mapping).to_expr()


# -- the trace ---------------------------------------------------------------------------------------


def _own_values(op) -> list:
    """The value expressions of one step (its filter, its new columns), without its input tables."""
    return [v for arg in op.__args__ for v in (arg if isinstance(arg, tuple) else (arg,)) if isinstance(v, ops.Value)] + \
        [v for v in getattr(op, "values", {}).values()] + [v for v in getattr(op, "metrics", {}).values()]


def _field_and_literal(cmp):
    left, right = cmp.left, cmp.right
    if isinstance(left, ops.Field) and isinstance(right, ops.Literal):
        return left, right
    if isinstance(right, ops.Field) and isinstance(left, ops.Literal):
        return right, left
    return None, None


def _merge(links: list[set]) -> list[set]:
    groups = []
    for link in links:
        touching = [g for g in groups if g & link]
        merged = set(link).union(*touching)
        groups = [g for g in groups if g not in touching] + [merged]
    return groups


def _depends_on(table):
    refs = _declared.get(table, ([], {}, set(), {}))[3]
    return next(iter(refs.values()), None)


# -- values ------------------------------------------------------------------------------------------


def _base(dtype, count: int, start: int = 0) -> list:
    """`count` distinct values of a type: A B C ..., 1 2 3 ..., dates from 2024-01-01."""
    if dtype.is_string():
        return [_letters(start + i) for i in range(count)]
    if dtype.is_integer():
        return [start + i + 1 for i in range(count)]
    if dtype.is_decimal():
        return [decimal.Decimal(f"{(start + i + 1) * 12.5:.2f}") for i in range(count)]
    if dtype.is_floating():
        spread = [15.0, 120.0, 35.5, 250.0, 80.0, 310.0, 42.0, 199.9]
        return [spread[(start + i) % len(spread)] + 1000 * ((start + i) // len(spread)) for i in range(count)]
    if dtype.is_boolean():
        return [(start + i) % 2 == 0 for i in range(count)]
    if dtype.is_timestamp():
        return [datetime.datetime(2024, 1, 1, 9) + datetime.timedelta(days=start + i, hours=i) for i in range(count)]
    if dtype.is_date():
        return [datetime.date(2024, 1, 1) + datetime.timedelta(days=start + i) for i in range(count)]
    if dtype.is_array():
        items = _base(dtype.value_type, 4)
        return [items[: (start + i) % 3] for i in range(count)]
    return [None] * count


def _letters(i: int) -> str:
    return string.ascii_uppercase[i % 26] * (1 + i // 26)


def _column(dtype, unique: bool, declared, hints: list) -> list:
    """The values of one column: declared values, then the literals of the code, then values of its type."""
    if declared is not None and declared != ANY:
        allowed = [a.lower for a in declared if a.lower == a.upper]  # a finite set
        if allowed:
            return [allowed[i % len(allowed)] for i in range(ROWS)]
    pool = []
    for kind, value in hints:
        pool += _around(kind, value)
    pool = list(dict.fromkeys(v for v in pool if v is not None and _fits(dtype, v)))
    pool += [v for v in _base(dtype, ROWS) if v not in pool]
    if unique:
        return pool[:ROWS]
    # columns repeat with different periods, so that their values combine in different ways across the rows
    literals = len(dict.fromkeys(v for _, v in hints))
    size = literals + 1 if dtype.is_string() and hints else max(4 if hints else 3, literals + 1) if not dtype.is_string() else 3
    repeated = pool[:size]
    return [repeated[i % len(repeated)] for i in range(ROWS)]


def _around(kind, value) -> list:
    """Values for a test on a literal: for `> 100`, two that pass, one that fails and the literal itself."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return [value]
    step = max(abs(value) * 0.5, 1)
    if isinstance(value, int):
        step = int(step)
    if kind in (ops.Greater, ops.GreaterEqual):
        return [value + step, value + 2 * step, value - step, value]
    if kind in (ops.Less, ops.LessEqual):
        return [value - step, value - 2 * step, value + step, value]
    return [value, value + step]


def _fits(dtype, value) -> bool:
    if dtype.is_string():
        return isinstance(value, str)
    if dtype.is_integer():
        return isinstance(value, int) and not isinstance(value, bool)
    if dtype.is_floating() or dtype.is_decimal():
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _linked(dtype, member: int, unique: bool) -> list:
    """Values of columns that a join compares: they share some values and differ in others.

    The first column gets A B C D E F; the next one keeps every second value and replaces the others
    (A G C H E I); so only a part of the rows match. A column that is not a key repeats its values.
    """
    base = _base(dtype, ROWS)
    if member:
        fresh = iter(_base(dtype, ROWS, start=ROWS * member))
        base = [v if i % 2 == 0 else next(fresh) for i, v in enumerate(base)]
    if unique:
        return base
    few = base[:3]
    return [few[i % len(few)] for i in range(ROWS)]
