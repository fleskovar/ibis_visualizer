"""Abstract interpretation of each traced step, over its SQL.

Each step compiles to SQL with its inputs as placeholder tables (`in0`, `in1`). sqlglot qualifies
the SQL, so each column names its source. Then a transfer rule for each SQL form (FROM, JOIN,
WHERE, GROUP BY, projection, DISTINCT, LIMIT, set operations) computes the facts of the output
from the facts of the inputs. The rules are sound: when a form is unknown, the fact is dropped.
"""

from __future__ import annotations

import datetime
import math
import re
from dataclasses import replace

import ibis
import portion as P
import sqlglot
from ibis.backends.sql.datatypes import DuckDBType
from sqlglot import exp
from sqlglot.optimizer import optimize
from sqlglot.optimizer.normalize import normalize
from sqlglot.optimizer.qualify import qualify

from ibis_anim._facts import ALWAYS, ANY, MAYBE, NEVER, Column, TableFacts, arithmetic, describe, hull, safe, source_facts, with_declared

NO_VALUE = object()
COMPARE = {exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
FLIP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "=": "=", "!=": "!="}


def derive(steps, names: dict) -> dict:
    """The facts of each table of the trace, keyed by its op."""
    facts, ranks = {}, {}  # one ranks dict for the whole trace: where each origin began
    for i, step in enumerate(steps):
        for k, table in enumerate(step.inputs):
            if table.op() not in facts:
                name = names.get(table.op(), f"in{k}")
                facts[table.op()] = source_facts(table, name, names)
                facts[table.op()].ranks = ranks
                ranks.update({f"{name}.{c}": -1 for c in table.columns if f"{name}.{c}" not in ranks})
        frontier = _frontier(step.output.op(), facts)
        ins = []
        for op in frontier:
            name, columns = names.get(op, "table"), facts[op].columns
            if name.isidentifier():  # a named table anchors the lineage; an unnamed result of a chain does not
                ranks.update({f"{name}.{c}": i for c in columns if f"{name}.{c}" not in ranks})
                columns = {c: replace(f, equal_to=f.equal_to | {f"{name}.{c}"}) for c, f in columns.items()}
            ins.append(TableFacts(columns, facts[op].keys))
        out = _step(step, frontier, ins, [names.get(op, "table") for op in frontier])
        facts[step.output.op()] = with_declared(out, step.output.op())  # e.g. the tests of a dbt model
        facts[step.output.op()].ranks = ranks
    return facts


def _frontier(output, known: dict) -> list:
    """The tables with known facts that the output reads, without going below them.

    Usually these are the inputs of the step. A chained join flattens into one JoinChain over the
    tables of the earlier joins, so its frontier is those tables.
    """
    found, stack, seen = [], list(output.__children__), set()
    while stack:
        node = stack.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if node in known:
            found.append(node)
        else:
            stack.extend(node.__children__)
    return found


def _substitute(op, mapping: dict):
    """`op` with each node of `mapping` replaced by its value.

    The walk stops at the nodes of `mapping`: it does not visit the plan below them. A full `replace` visits the
    whole plan, so on a long pipeline each call costs as much as all the steps before it.
    """
    if not mapping:
        return op
    own, stack = set(), [op]
    while stack:
        node = stack.pop()
        if node not in own:
            own.add(node)
            if node not in mapping:
                stack.extend(node.__children__)
    return op.replace(mapping, filter=own.__contains__)


def _step(step, frontier: list, ins: list[TableFacts], labels: list[str]) -> TableFacts:
    names = {f"in{k}": f"in{k}" for k in range(len(ins))}
    placeholders = {op: ibis.table(op.schema, name=n).op() for op, n in zip(frontier, names)}
    schema = {n: {c: DuckDBType.from_ibis(d).sql("duckdb") for c, d in op.schema.items()} for op, n in zip(frontier, names)}
    try:
        tree = sqlglot.parse_one(ibis.to_sql(_substitute(step.output.op(), placeholders).to_expr(), dialect="duckdb"), read="duckdb")
        try:
            tree = optimize(tree, schema=schema, dialect="duckdb")
        except Exception:  # some shapes fail an optimizer rule; qualifying alone is enough for the rules
            tree = qualify(tree, schema=schema, dialect="duckdb")
        out = _query(tree, dict(zip(names, ins)))
    except Exception as exc:  # ponytail: a SQL shape that the rules do not read gives no facts, which is sound
        out = TableFacts({}, rows="?", warnings=[f"no facts for this step ({type(exc).__name__})"])
    out.columns = {c: out.column(c) for c in step.output.columns}
    out.keys = [k for k in dict.fromkeys(out.keys) if k <= set(out.columns)]
    labels = dict(zip(names, labels))
    out.warnings = [_relabel(w, labels) for w in dict.fromkeys(out.warnings)]
    return out


def _pretty(e) -> str:
    """SQL of an expression without table qualifiers and quotes, for messages."""
    unwindowed = e.copy().transform(lambda n: n.this if isinstance(n, exp.Window) else n)  # `sum(x) OVER (...)` reads as `sum(x)`
    bare = unwindowed.transform(lambda n: exp.column(n.name) if isinstance(n, exp.Column) else n)
    return bare.sql(dialect="duckdb").replace('"', "")


def _relabel(text: str, labels: dict) -> str:
    for placeholder, label in labels.items():
        text = text.replace(placeholder, label)
    return text


# -- relations ---------------------------------------------------------------------------------------


def _query(node, env: dict) -> TableFacts:
    if isinstance(node, exp.Subquery):
        return _query(node.this, env)
    ctes = node.args.get("with_") or node.args.get("with")  # sqlglot 30 names it with_
    if ctes:
        env = env | {cte.alias: _query(cte.this, env) for cte in ctes.expressions}
    if isinstance(node, (exp.Union, exp.Intersect, exp.Except)):
        return _set_operation(node, env)
    if isinstance(node, exp.Select):
        return _select(node, env)
    raise ValueError(f"unknown query form {type(node).__name__}")


def _source(node, env: dict) -> tuple[str, TableFacts, str]:
    """(alias, facts, table name) of a FROM or JOIN source."""
    if isinstance(node, exp.Table):
        return node.alias_or_name, env[node.name], node.name
    if isinstance(node, exp.Subquery):
        return node.alias, _query(node.this, env), node.alias
    raise ValueError(f"unknown source {type(node).__name__}")


def _select(sel: exp.Select, env: dict) -> TableFacts:
    source = (sel.args.get("from_") or sel.args.get("from")).this  # sqlglot 30 names it from_
    alias, first, first_name = _source(source, env)
    cols = {(alias, c): f for c, f in first.columns.items()}
    keys = [frozenset((alias, c) for c in k) for k in first.keys]
    from_input = re.fullmatch(r"in\d+", first_name) is not None  # else a CTE or a subquery of this step
    rows, warnings = ("= input", []) if from_input else (first.rows or "?", list(first.warnings))
    can_empty = False if from_input else first.can_empty  # an empty result from a non-empty input
    if source.args.get("sample"):
        cols, rows, can_empty = {k: f.narrowed() for k, f in cols.items()}, "≤ input", True
    for join in sel.args.get("joins") or []:
        cols, keys, rows, join_can_empty = _join(join, cols, keys, rows, first_name, env, warnings)
        can_empty = can_empty or join_can_empty
    if sel.args.get("where") is not None:  # no row has to pass the condition
        cols = _where(sel.args["where"].this, cols, warnings)
        rows, can_empty = "≤ input", True
    group = sel.args.get("group")
    aggregated = bool(group) or any(_has_aggregate(p) for p in sel.expressions)
    out = {p.alias_or_name: _expr(p.unalias(), cols, bool(group), warnings) for p in sel.expressions}
    bare = {(p.unalias().table, p.unalias().name): p.alias_or_name for p in sel.expressions if isinstance(p.unalias(), exp.Column)}
    if group:
        by = [bare.get((g.table, g.name)) for g in group.expressions if isinstance(g, exp.Column)]
        keys = [frozenset(by)] if by and None not in by and len(by) == len(group.expressions) else []
        rows = "≤ input"
    elif aggregated:  # an aggregate without GROUP BY gives one row: each column identifies it
        keys, rows, can_empty = [frozenset({n}) for n in out], "1", False
    else:
        keys = [frozenset(bare[c] for c in k) for k in keys if all(c in bare for c in k)]
    if any(p.find(exp.Explode, exp.Unnest) for p in sel.expressions):  # UNNEST in the projection makes rows
        keys, rows, can_empty = [], "≠ input", True  # an empty array gives no row
        out = {c: f.narrowed() for c, f in out.items()}
    if sel.args.get("distinct"):
        keys.append(frozenset(out))
        rows = "≤ input"
    limit = sel.args.get("limit")
    if limit is not None:
        n = _literal(limit.expression)
        rows = f"≤ {n}" if isinstance(n, int) else "≤ input"
        can_empty = can_empty or n == 0
        if n == 1:
            keys += [frozenset({c}) for c in out]
        out = {c: f.narrowed() for c, f in out.items()}
    return TableFacts(out, keys, rows, warnings, can_empty=can_empty)


def _then(before: str, after: str) -> str:
    """The row relation of two operations one after the other, for example '≤ input' then '= input'."""
    if before == "= input":
        return after
    if after == "= input" or after == before:
        return before
    return "?"


def _join(join: exp.Join, cols, keys, rows, left_name, env, warnings):
    side, kind = (join.side or "").upper(), (join.kind or "").upper()
    before = rows  # a chained join comes after the joins before it
    target = join.this
    if isinstance(target, exp.Unnest):  # each array element becomes a row: keys are lost
        alias = target.args.get("alias")
        elements = {(alias.name, c.name): Column() for c in alias.columns} if alias else {}
        return {k: f.narrowed() for k, f in cols.items()} | elements, [], "≠ input", True
    alias, right, right_name = _source(target, env)
    if isinstance(target, exp.Subquery):
        warnings.extend(right.warnings)
    rcols = {(alias, c): f for c, f in right.columns.items()}
    rkeys = [frozenset((alias, c) for c in k) for k in right.keys]
    pairs = []
    on = join.args.get("on")
    for cond in _conjuncts(on) if on is not None else []:
        if isinstance(cond, exp.EQ) and isinstance(cond.this, exp.Column) and isinstance(cond.expression, exp.Column):
            a, b = (cond.this.table, cond.this.name), (cond.expression.table, cond.expression.name)
            pairs.append((a, b) if b in rcols else (b, a))
    left_keys_in_join, right_keys_in_join = {a for a, _ in pairs}, {b for _, b in pairs}
    right_unique = any(k <= right_keys_in_join for k in rkeys)
    left_unique = any(k <= left_keys_in_join for k in keys)
    new_keys = [kl | kr for kl in keys for kr in rkeys]
    if right_unique and side in ("", "INNER", "LEFT"):  # each left row appears once at most
        new_keys += keys
    if left_unique and side in ("", "INNER", "RIGHT"):  # each right row appears once at most
        new_keys += rkeys
    if side == "LEFT":
        rcols = {k: replace(f.narrowed(), nulls=MAYBE) for k, f in rcols.items()}  # every left row stays, once at least
        rows = "= input" if right_unique else "≥ input"
    elif side in ("RIGHT", "FULL"):
        cols = {k: replace(f.narrowed(), nulls=MAYBE) for k, f in cols.items()}
        rcols = {k: f.narrowed() if side == "RIGHT" else replace(f.narrowed(), nulls=MAYBE) for k, f in rcols.items()}
        rows = "?"
    else:
        cols, rcols = {k: f.narrowed() for k, f in cols.items()}, {k: f.narrowed() for k, f in rcols.items()}
        rows = "≤ input" if right_unique else "?"
    for a, b in pairs:
        both = safe(lambda: cols[a].values & rcols[b].values)
        if both.empty and cols[a].values != ANY and rcols[b].values != ANY:
            warnings.append(f"{left_name}.{a[1]} and {right_name}.{b[1]} have no value in common: no row matches")
        origins_a, origins_b = cols[a].subset_of | cols[a].equal_to, rcols[b].subset_of | rcols[b].equal_to
        rcols[b] = replace(rcols[b], values=both, subset_of=rcols[b].subset_of | origins_a)
        if side != "LEFT":
            cols[a] = replace(cols[a], values=both, nulls=NEVER, subset_of=cols[a].subset_of | origins_b)
            rcols[b] = replace(rcols[b], nulls=NEVER)
    if pairs and not right_unique and side in ("", "INNER", "LEFT"):
        joined = ", ".join(f"{right_name}.{b[1]}" for _, b in pairs)
        warnings.append(f"rows of {left_name} can repeat: {joined} is not known to be unique (fan-out)")
    if kind == "CROSS" or not pairs:
        rows = "?"
    # an outer join keeps the rows of its outer side; an inner, semi or anti join can match no row
    can_empty = side not in ("LEFT", "RIGHT", "FULL") and kind != "CROSS"
    return cols | rcols, list(dict.fromkeys(new_keys)), _then(before, rows), can_empty


def _where(condition, cols, warnings):
    cols = dict(cols)
    for cond in _conjuncts(condition):
        if isinstance(cond, exp.Boolean) and not cond.this:  # the optimizer makes a contradiction FALSE: a > 100 AND a < 50
            warnings.append("the condition is never true: the filter keeps no row")
            return {k: replace(f, values=P.empty(), nulls=NEVER) for k, f in cols.items()}
        if isinstance(cond, exp.EQ) and isinstance(cond.this, exp.Column) and isinstance(cond.expression, exp.Column):
            a, b = (cond.this.table, cond.this.name), (cond.expression.table, cond.expression.name)
            if a in cols and b in cols:
                both = safe(lambda: cols[a].values & cols[b].values)
                cols[a], cols[b] = replace(cols[a], values=both, nulls=NEVER), replace(cols[b], values=both, nulls=NEVER)
            continue
        found = _condition(cond)
        if found is None or found[0] not in cols:
            continue
        key, values, nulls = found
        col = cols[key]
        values = col.values if values is None else safe(lambda: col.values & values, col.values)
        cols[key] = replace(col, values=values, nulls=nulls or col.nulls)
        if values.empty and nulls != ALWAYS:
            warnings.append(f"{key[1]} has no possible value after `{_pretty(cond)}`: the filter keeps no row")
    if any(f.values.empty and f.nulls == NEVER for f in cols.values()):  # one column with no value: no row, so no column has one
        return {k: replace(f, values=P.empty(), nulls=NEVER) for k, f in cols.items()}  # the same as a FALSE condition
    return {k: f.narrowed() for k, f in cols.items()}


def _set_operation(node, env) -> TableFacts:
    left, right = _query(node.left, env), _query(node.right, env)
    distinct = bool(node.args.get("distinct"))
    out = {}
    for (name, a), b in zip(left.columns.items(), right.columns.values()):
        a_origins, b_origins = a.subset_of | a.equal_to, b.subset_of | b.equal_to
        if isinstance(node, exp.Union):
            out[name] = Column(safe(lambda: a.values | b.values), NEVER if a.nulls == b.nulls == NEVER else MAYBE,
                               subset_of=a_origins & b_origins)
        elif isinstance(node, exp.Intersect):
            out[name] = Column(safe(lambda: a.values & b.values, a.values), a.nulls, subset_of=a_origins | b_origins)
        else:
            out[name] = Column(a.values, a.nulls, subset_of=a_origins)
    keys = [frozenset(out)] if distinct else []
    if isinstance(node, exp.Except):
        keys += left.keys
    rows = "= sum of inputs" if isinstance(node, exp.Union) and not distinct else "≤ input"
    can_empty = (left.can_empty and right.can_empty) if isinstance(node, exp.Union) else True  # intersect, except
    return TableFacts(out, keys, rows, left.warnings + right.warnings, can_empty=can_empty)


# -- conditions --------------------------------------------------------------------------------------


def _conjuncts(condition) -> list:
    condition = normalize(condition.copy(), dnf=False)
    return list(condition.flatten()) if isinstance(condition, exp.And) else [condition]


def _column_key(e) -> tuple | None:
    e = e.unnest()
    return (e.table, e.name) if isinstance(e, exp.Column) else None


def _condition(c):
    """(column, values that satisfy c, null effect) for a condition on one column, else None.

    The value set always contains every value that satisfies c, so intersecting with it is sound.
    """
    c = c.unnest()
    if type(c) in COMPARE:
        left, right, op = c.this, c.expression, COMPARE[type(c)]
        if _column_key(right) and _literal(left) is not NO_VALUE:
            left, right, op = right, left, FLIP[op]
        key, value = _column_key(left), _literal(right)
        if key and value is not NO_VALUE:
            return key, _ray(op, value), NEVER
    if isinstance(c, exp.In) and _column_key(c.this):
        values = [_literal(v) for v in c.expressions]
        if values and NO_VALUE not in values:
            return _column_key(c.this), P.Interval(*(P.singleton(v) for v in values)), NEVER
    if isinstance(c, exp.Between) and _column_key(c.this):
        low, high = _literal(c.args["low"]), _literal(c.args["high"])
        if NO_VALUE not in (low, high):
            return _column_key(c.this), P.closed(low, high), NEVER
    if isinstance(c, exp.Is) and isinstance(c.expression, exp.Null) and _column_key(c.this):
        return _column_key(c.this), P.empty(), ALWAYS
    if isinstance(c, exp.Not):
        inner = c.this.unnest()
        if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null) and _column_key(inner.this):
            return _column_key(inner.this), None, NEVER
        found = _condition(inner) if isinstance(inner, (exp.EQ, exp.In)) else None
        if found:
            return found[0], safe(lambda: ANY - found[1]), NEVER
        return None
    if isinstance(c, (exp.Or, exp.And)):
        a, b = _condition(c.this), _condition(c.expression)
        if a and b and a[0] == b[0] and None not in (a[1], b[1]) and ALWAYS not in (a[2], b[2]):
            values = safe(lambda: a[1] | b[1]) if isinstance(c, exp.Or) else safe(lambda: a[1] & b[1])
            return a[0], values, NEVER
    return None


def _ray(op: str, v) -> P.Interval:
    return {"=": lambda: P.singleton(v), "!=": lambda: ANY - P.singleton(v), ">": lambda: P.open(v, P.inf),
            ">=": lambda: P.closed(v, P.inf), "<": lambda: P.open(-P.inf, v), "<=": lambda: P.closed(-P.inf, v)}[op]()


def _literal(e):
    e = e.unnest()
    if isinstance(e, exp.Literal):
        if e.is_string:
            return e.this
        return float(e.this) if any(ch in e.this for ch in ".eE") else int(e.this)
    if isinstance(e, exp.Neg):
        v = _literal(e.this)
        return -v if isinstance(v, (int, float)) else NO_VALUE
    if isinstance(e, exp.Boolean):
        return bool(e.this)
    if isinstance(e, exp.Cast) and isinstance(e.this, exp.Literal) and e.this.is_string:
        try:
            if e.to.is_type(exp.DataType.Type.DATE):
                return datetime.date.fromisoformat(e.this.this)
            if e.to.is_type(*exp.DataType.TEMPORAL_TYPES):
                return datetime.datetime.fromisoformat(e.this.this)
        except ValueError:
            return NO_VALUE
    return NO_VALUE


# -- expressions -------------------------------------------------------------------------------------


def _has_aggregate(e) -> bool:
    return any(not agg.find_ancestor(exp.Window) for agg in e.find_all(exp.AggFunc))


def _expr(e, cols: dict, grouped: bool, warnings: list) -> Column:
    """The facts of the value of expression e in each output row."""
    e = e.unnest()
    sub = lambda x: _expr(x, cols, grouped, warnings)  # noqa: E731
    if isinstance(e, exp.Column):
        if e.text("db"):  # a field of a struct column: "t1"."__pivoted__"."month"
            return dict(cols.get((e.text("db"), e.table), Column()).fields).get(e.name, Column())
        return cols.get((e.table, e.name), Column())
    if isinstance(e, exp.Dot):  # the optimizer writes a struct field as struct_column.field
        return dict(sub(e.this).fields).get(e.expression.name, Column())
    if isinstance(e, (exp.Explode, exp.Unnest)):
        return _unnested(e, sub)
    if isinstance(e, exp.Null):
        return Column(P.empty(), ALWAYS)
    if (v := _literal(e)) is not NO_VALUE:
        return Column(P.singleton(v), NEVER)
    if isinstance(e, (exp.Add, exp.Sub, exp.Mul)):
        a, b = sub(e.this), sub(e.expression)
        op = {exp.Add: "+", exp.Sub: "-", exp.Mul: "*"}[type(e)]
        return Column(arithmetic(op, a.values, b.values), NEVER if a.nulls == b.nulls == NEVER else MAYBE)
    if isinstance(e, exp.Neg):
        a = sub(e.this)
        return Column(arithmetic("-", P.singleton(0), a.values), a.nulls)
    if isinstance(e, exp.Div):
        a, b = sub(e.this), sub(e.expression)
        if b.values != ANY and safe(lambda: not (b.values & P.singleton(0)).empty, False):
            warnings.append(f"division by `{_pretty(e.expression)}`, which can be 0")
        return Column(ANY, MAYBE)
    if isinstance(e, exp.Coalesce):
        parts = [sub(x) for x in [e.this, *e.expressions]]
        values = P.empty()
        for part in parts:
            values = safe(lambda: values | part.values)
            if part.nulls == NEVER:
                return Column(values, NEVER)
        return Column(values, MAYBE)
    if isinstance(e, exp.Case):
        branches = [sub(i.args["true"]) for i in e.args.get("ifs", [])]
        default = e.args.get("default")
        branches.append(sub(default) if default is not None else Column(P.empty(), ALWAYS))
        values = P.empty()
        for b in branches:
            values = safe(lambda: values | b.values)
        return Column(values, NEVER if all(b.nulls == NEVER for b in branches) else MAYBE)
    if isinstance(e, (exp.Cast, exp.TryCast)):
        return _cast(e, sub(e.this), warnings)
    if isinstance(e, exp.Round):
        a = sub(e.this)
        lo, hi = (a.values.lower, a.values.upper) if not a.values.empty else (-P.inf, P.inf)
        widen = lambda x, f: x if x in (P.inf, -P.inf) else f(x)  # noqa: E731
        return Column(safe(lambda: P.closed(widen(lo, math.floor), widen(hi, math.ceil))), a.nulls)
    if isinstance(e, (exp.Predicate, exp.Connector, exp.Not)):
        return Column(P.singleton(False) | P.singleton(True), MAYBE)
    if isinstance(e, exp.Window):
        return _window(e, sub)
    if isinstance(e, exp.Filter):  # AGG(x) FILTER (WHERE ...): a group can have no matching row
        return replace(sub(e.this), nulls=MAYBE)
    if isinstance(e, exp.AggFunc):
        return _aggregate(e, sub, grouped)
    return Column()


def _unnested(e, sub) -> Column:
    """The element of UNNEST(array). A literal array of structs (as pivot_longer makes) gives facts per field."""
    array = e.this.this if isinstance(e.this, exp.Cast) else e.this
    if not isinstance(array, exp.Array) or not all(isinstance(x, exp.Struct) for x in array.expressions):
        return Column()
    fields: dict[str, list[Column]] = {}
    for struct in array.expressions:
        for prop in struct.expressions:
            if isinstance(prop, exp.PropertyEQ):
                fields.setdefault(prop.this.name, []).append(sub(prop.expression))
    return Column(fields=tuple((name, _union(parts)) for name, parts in fields.items()))


def _union(parts: list[Column]) -> Column:
    """A column whose value comes from any of `parts`."""
    values = P.empty()
    for part in parts:
        values = safe(lambda: values | part.values)
    origins = [p.subset_of | p.equal_to for p in parts]
    return Column(values, NEVER if all(p.nulls == NEVER for p in parts) else MAYBE,
                  subset_of=frozenset.intersection(*origins) if origins else frozenset())


def _cast(e, inner: Column, warnings: list) -> Column:
    to = e.to
    source = e.this.type
    if source is not None and source.is_type(*exp.DataType.REAL_TYPES) and to.is_type(*exp.DataType.INTEGER_TYPES):
        warnings.append(f"cast of `{_pretty(e.this)}` to {to.sql(dialect='duckdb')} drops the fraction")
    if source is not None and source.is_type(*exp.DataType.TEXT_TYPES) and not to.is_type(*exp.DataType.TEXT_TYPES):
        warnings.append(f"try_cast of `{_pretty(e.this)}` gives null for a text that is not a {to.sql(dialect='duckdb')}"
                        if isinstance(e, exp.TryCast) else
                        f"cast of `{_pretty(e.this)}` to {to.sql(dialect='duckdb')} fails on a text that is not a {to.sql(dialect='duckdb')}")
    nulls = MAYBE if isinstance(e, exp.TryCast) else inner.nulls
    if to.is_type(*exp.DataType.INTEGER_TYPES) and not inner.values.empty and inner.values != ANY:
        lo, hi = inner.values.lower, inner.values.upper
        return Column(safe(lambda: P.closed(lo if lo == -P.inf else math.floor(lo), hi if hi == P.inf else math.ceil(hi))), nulls)
    return Column(ANY, nulls)


def _aggregate(e, sub, grouped: bool) -> Column:
    """The value of an aggregate over one group. With GROUP BY, each group has at least one row."""
    if isinstance(e, exp.Count):
        counted_never_null = isinstance(e.this, exp.Star) or sub(e.this).nulls == NEVER
        return Column(P.closed(1 if grouped and counted_never_null else 0, P.inf), NEVER)
    x = sub(e.this)
    nulls = NEVER if grouped and x.nulls == NEVER else MAYBE
    if isinstance(e, exp.Sum):
        if x.values.empty or x.values == ANY:
            return Column(ANY, nulls)
        lo, hi = x.values.lower, x.values.upper
        values = P.closed(0, P.inf) if safe(lambda: lo >= 0, False) else P.closed(-P.inf, 0) if safe(lambda: hi <= 0, False) else ANY
        return Column(values, nulls)
    if isinstance(e, (exp.Min, exp.Max, exp.AnyValue, exp.First, exp.Last)):
        return Column(x.values, nulls, subset_of=x.subset_of | x.equal_to)
    if isinstance(e, exp.Avg):
        return Column(hull(x.values) if x.values != ANY else ANY, nulls)
    return Column()


def _window(e: exp.Window, sub) -> Column:
    func = e.this
    name = (func.name if isinstance(func, exp.Anonymous) else func.sql_name() if hasattr(func, "sql_name") else "").upper()
    if name in ("RANK", "DENSE_RANK", "ROW_NUMBER"):
        return Column(P.closed(1, P.inf), NEVER)
    if isinstance(func, exp.Count):
        return Column(P.closed(0, P.inf), NEVER)
    if isinstance(func, exp.AggFunc):
        return replace(_aggregate(func, sub, grouped=False), nulls=MAYBE)  # a window frame can be empty
    return Column()


# -- report ------------------------------------------------------------------------------------------


def column_text(facts: TableFacts, name: str, table_name: str = "") -> str:
    """Short facts of one column: 'unique · {paid} · not null · ⊆ customers.customer_id'.

    The lineage names the most upstream table that still holds (a source table first, then the
    earliest named table). So it stays the same from step to step until the relation really changes.
    """
    col = facts.column(name)
    parts = ["unique"] if facts.unique(name) else []
    if values := describe(col.values):
        parts.append(values)
    if col.nulls == NEVER:
        parts.append("not null")
    elif col.nulls == ALWAYS:
        parts.append("always null")
    own = {f"{table_name}.{name}"}
    first = lambda origins: sorted(origins, key=lambda o: (facts.ranks.get(o, -1), o))[:1]  # noqa: E731
    parts += [f"= {o}" for o in first(col.equal_to - own)]
    parts += [f"⊆ {o}" for o in first(col.subset_of - own - col.equal_to)]
    return " · ".join(parts)


def report(steps, facts: dict, names: dict) -> str:
    lines = []
    for i, step in enumerate(steps, 1):
        out = facts[step.output.op()]
        name = names.get(step.output.op(), "result")
        inputs = [names.get(t.op(), "table") for t in step.inputs]
        empty = " · can be empty" if out.can_empty else ""
        lines.append(f"{i:>2}. {step.function}() › {step.op}({', '.join(inputs)}) → {name}   rows {out.rows or '?'}{empty}")
        if out.keys:
            keys = ["all columns" if k == set(out.columns) else "(" + ", ".join(sorted(k)) + ")" for k in out.keys[:3]]
            lines.append("      keys: " + "; ".join(keys))
        for c in step.output.columns:
            text = column_text(out, c, name)
            if text:
                lines.append(f"      {c}: {text}")
        lines += [f"      ⚠ {w}" for w in out.warnings]
    return "\n".join(lines)
