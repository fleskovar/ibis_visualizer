"""Record the ibis table operations that a Python function performs."""

from __future__ import annotations

import ast
import functools
import inspect
import linecache
import textwrap
import os
import sys
import sysconfig
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from traceback import FrameSummary

import ibis.expr.operations as ops
import ibis.expr.types as ir
from ibis.expr.types.groupby import GroupedTable
from ibis.expr.types.joins import Join

TABLE_METHODS = (
    "filter", "select", "mutate", "drop", "rename", "relocate", "join", "aggregate", "agg",
    "order_by", "distinct", "limit", "head", "pivot_longer", "pivot_wider", "union", "intersect",
    "difference", "unnest", "drop_null", "fill_null", "cast", "try_cast", "sample",
)
GROUPED_METHODS = ("aggregate", "agg", "mutate", "select")

# Frames from these folders are library code, not code that the user wants to step through.
_LIBRARY_DIRS = tuple(
    os.path.normcase(p)
    for p in (os.path.dirname(__file__), sysconfig.get_paths()["stdlib"], sysconfig.get_paths()["purelib"])
)


@dataclass
class Step:
    """One ibis operation, as the visual debugger shows it."""

    op: str
    inputs: list[ir.Table]
    output: ir.Table
    labels: list[str]  # variable names of the inputs in the calling frame
    code: str  # source text of the call
    stack: list[FrameSummary]  # user frames, outermost first
    args: tuple
    kwargs: dict
    group_by: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)  # input columns that the arguments reference
    created: list[str] = field(default_factory=list)  # output columns that the step adds or overwrites

    @property
    def function(self) -> str:
        return self.stack[-1].name if self.stack else "?"


_steps: list[Step] | None = None  # None means "do not record"
_depth = 0  # ponytail: module globals, so recording is single-threaded only.


@contextmanager
def recording(steps: list[Step] | None):
    """Record the steps into `steps` inside the block. `None` stops the recording."""
    global _steps
    previous, _steps = _steps, steps
    try:
        yield steps
    finally:
        _steps = previous


def trace(func, *args, **kwargs) -> tuple[object, list[Step]]:
    """Call `func` and return its result with the ibis steps that it performed.

    A dbt-ibis model (`@depends_on(...)`) called without arguments gets its inputs from its project:
    the models upstream of it run first, under the same recording.
    """
    with recording([]) as steps:
        if not args and not kwargs and getattr(func, "depends_on", None) is not None:
            from ibis_anim._dbt import run

            return run(func), steps
        return func(*args, **kwargs), steps


def install():
    """Wrap the ibis table methods so that they record a step while recording is on."""
    # Join keeps its own copies of the Table methods, so it needs its own wrappers.
    for cls, names in ((ir.Table, TABLE_METHODS), (Join, TABLE_METHODS), (GroupedTable, GROUPED_METHODS)):
        for name in names:
            orig = cls.__dict__.get(name)
            if orig is not None and not getattr(orig, "_ibis_anim", False):
                setattr(cls, name, _wrap(name, orig))


def _wrap(name, orig):
    @functools.wraps(orig)
    def wrapper(self, *args, **kwargs):
        global _depth
        if _steps is None or _depth:  # ibis calls its own methods: record only the outermost call
            return orig(self, *args, **kwargs)
        _depth += 1
        try:
            out = orig(self, *args, **kwargs)
            if isinstance(out, ir.Table):
                try:
                    columns = _named_columns(args, kwargs)  # columns built earlier, outside a table call
                    _steps.extend(step for step in columns if all(step.output.op() != s.output.op() for s in _steps))
                    _steps.append(_make_step(name, self, out, args, kwargs, columns))
                except Exception:  # ponytail: a step that cannot be recorded yet (e.g. an unfinished join) is skipped, never raised
                    pass
            return out
        finally:
            _depth -= 1

    wrapper._ibis_anim = True
    return wrapper


def _make_step(name, receiver, out, args, kwargs, columns=()) -> Step:
    grouped = isinstance(receiver, GroupedTable)
    src = receiver.table.to_expr() if grouped else receiver
    inputs = [src] + [a for a in (*args, *kwargs.values()) if isinstance(a, ir.Table)]
    tables = len(inputs)
    inputs += [c.output for c in columns]  # the one-column tables of the named columns that the call reads
    in_cols = {c for t in inputs for c in t.columns}
    frames = _user_frames()
    group_by = [g.name for g in receiver.groupings] if grouped else _names(kwargs.get("by"), src)
    used = [c for c in dict.fromkeys(_names([args, kwargs], src)) if c in in_cols]
    if isinstance(out.op(), ops.Project):  # a projected value that is not the same input field is computed
        created = [c for c, v in out.op().values.items() if not (isinstance(v, ops.Field) and v.name == c)]
    else:
        created = [c for c in out.columns if c not in in_cols or c in kwargs]
    return Step(
        op=f"group_by → {name}" if grouped else name,
        inputs=inputs,
        output=out,
        labels=[_label(t, frames) for t in inputs[:tables]] + [c.created[0] for c in columns],
        code=_call_source(frames[-1], ".group_by(" if grouped else f".{name}(") if frames else "",
        stack=[FrameSummary(f.f_code.co_filename, f.f_lineno, f.f_code.co_qualname) for f in frames],
        args=args,
        kwargs=kwargs,
        group_by=list(dict.fromkeys(group_by)),
        used=used,
        created=created,
    )


def _named_columns(args, kwargs) -> list[Step]:
    """A 'column' step for each argument that is a column built earlier and held by a variable.

    `label = ibis.coalesce(t.nick, t.player)` makes no table call, so nothing records it. When a table
    call reads `label`, this finds the variable and its assignment in the source, and records the column
    as a one-column table, `t.select(label=...)`, made at the line of the assignment.
    """
    frames = _user_frames()
    if not frames:
        return []
    frame, found = frames[-1], []
    values = [v for v in (*args, *kwargs.values()) for v in (v if isinstance(v, (list, tuple)) else (v,))]
    for value in values:
        if not isinstance(value, ir.Column) or isinstance(value.op(), ops.Field):
            continue  # a bare field is a column of the table already
        name = next((k for scope in (frame.f_locals, frame.f_globals) for k, v in scope.items() if v is value), None)
        relations = {f.rel for f in value.op().find(ops.Field, filter=ops.Value)}
        if name is None or len(relations) != 1:
            continue  # an expression written in the call itself is part of the step
        table = next(iter(relations)).to_expr()
        line, text = _assignment(frame, name) or (frame.f_lineno, name)
        stack = [FrameSummary(f.f_code.co_filename, f.f_lineno, f.f_code.co_qualname) for f in frames]
        stack[-1] = FrameSummary(frame.f_code.co_filename, line, frame.f_code.co_qualname)
        found.append(Step(
            op="column",
            inputs=[table],
            output=table.select(**{name: value}),
            labels=[_label(table, frames)],
            code=f"{name} = {' '.join(text.split())}" if text != name else name,
            stack=stack,
            args=(),
            kwargs={name: value},
            used=list(dict.fromkeys(f.name for f in value.op().find(ops.Field, filter=ops.Value))),
            created=[name],
        ))
    return found


def _assignment(frame, name: str) -> tuple[int, str] | None:
    """(line, source of the value) of the last assignment `name = ...` before the current line of `frame`."""
    try:
        lines, start = inspect.getsourcelines(frame.f_code)
        source = textwrap.dedent("".join(lines))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return None
    offset = start - 1 if start > 0 else 0  # a module starts at line 0, a function at its def line
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            line = node.lineno + offset
            if any(isinstance(t, ast.Name) and t.id == name for t in targets) and line <= frame.f_lineno:
                if best is None or line > best[0]:
                    best = (line, ast.get_source_segment(source, node.value) or name)
    return best


def _names(obj, table: ir.Table) -> list[str]:
    """Column names that `obj` references, in order of first use."""
    match obj:
        case None | ir.Table():
            return []
        case str():
            return [obj]
        case ir.Expr():
            return [f.name for f in obj.op().find(ops.Field, filter=ops.Value)]
        case list() | tuple():
            return [n for item in obj for n in _names(item, table)]
        case dict():
            return _names(list(obj.values()), table)
        case _:  # deferred `_.col` expressions and selectors
            try:
                return _names(table.bind(obj), table)
            except Exception:
                return []


def _user_frames() -> list:
    frames, f = [], sys._getframe(1)
    while f is not None and f.f_code is not trace.__code__:
        if not os.path.normcase(f.f_code.co_filename).startswith(_LIBRARY_DIRS):
            frames.append(f)
        f = f.f_back
    return frames[::-1]


def _label(table: ir.Table, frames: list) -> str:
    """The variable name that holds `table` in the innermost user frame."""
    op = table.op()
    for f in reversed(frames):
        for scope in (f.f_locals, f.f_globals):  # tables defined at module level are globals of the function
            name = next((k for k, v in scope.items() if isinstance(v, ir.Table) and v.op() == op and not k.startswith("_")), None)
            if name:
                return name
    if isinstance(op, ops.UnboundTable):
        return op.name
    return next((f"{s.op} result" for s in reversed(_steps) if s.output.op() == op), "table")


def _call_source(frame, marker: str) -> str:
    """Source text of the call that `frame` executes, from `marker` onwards when the call is a chain."""
    start, end, col, end_col = next(islice(frame.f_code.co_positions(), frame.f_lasti // 2, None))
    if None in (start, end, col, end_col):
        return linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip()
    lines = [linecache.getline(frame.f_code.co_filename, n).encode() for n in range(start, end + 1)]
    lines[-1] = lines[-1][:end_col]
    lines[0] = lines[0][col:]
    text = " ".join(b"".join(lines).decode().split())
    cut = text.rfind(marker)  # in `a.filter(x).mutate(y)`, show only `mutate(y)`
    return text if cut <= 0 or text[:cut].replace(".", "_").isidentifier() else text[cut + 1:]
