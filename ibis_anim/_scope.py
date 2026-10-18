"""Plan the tables panel: which tables are in scope at each step, like the variables view of a debugger.

A table is one ibis relation (its op). It appears on the panel the first time a step reads it. The
result of a step takes the place of its first input when no later step reads that input, the way
`orders = orders.filter(...)` rebinds a variable. A table that no later step reads leaves the panel
`keep_idle` steps after its last use. The video renders after the run, so the plan knows the future.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeFrame:
    """The panel around one step. Each key is an op."""

    shown: list  # tables on the panel during the step, in panel order
    inputs: list  # tables that the step reads
    output: object  # the table that the step makes
    after: list  # tables on the panel after the step, in panel order
    replaced: object | None  # the input whose place the output takes
    expired: list  # tables that leave the panel after the step
    dead: set  # tables on the panel during the step that no later step reads
    dead_after: set  # tables on the panel after the step that no later step reads


_given: dict = {}  # table op → a name that a loader gave it, for example the name of a dbt model


def name_table(table, name: str):
    """Give `table` a name before the variables that hold it, for example the name of its dbt model."""
    _given[table.op()] = name
    return table


def table_names(steps) -> dict:
    """A name for each table: a given name, else the first variable that holds it, else a name from the
    operation that made it (`filtered orders`, `orders × stores`). Only the name of a variable is an identifier."""
    names = {op: _given[op] for step in steps for op in (step.output.op(), *(t.op() for t in step.inputs)) if op in _given}
    for step in steps:
        for table, label in zip(step.inputs, step.labels):
            if label.isidentifier() and label != "table":
                names.setdefault(table.op(), label)
    for step in steps:
        for table, label in zip(step.inputs, step.labels):
            names.setdefault(table.op(), label)
        names.setdefault(step.output.op(), _derived_name(step, names))
    return names


_PHRASES = {  # the name of an unnamed result; {} is the name of the first input
    "filter": "filtered {}", "order_by": "sorted {}", "distinct": "distinct {}", "sample": "sample of {}",
    "drop_null": "{} without nulls", "fill_null": "{}, nulls filled", "rename": "renamed {}", "relocate": "reordered {}",
    "cast": "cast {}", "try_cast": "cast {}", "pivot_wider": "wide {}", "pivot_longer": "long {}", "unnest": "unnested {}",
}
_SYMBOLS = {"join": "×", "union": "∪", "intersect": "∩", "difference": "−"}


def _few(names, n: int = 2) -> str:
    return ", ".join(names[:n]) + (", …" if len(names) > n else "")


def _derived_name(step, names: dict) -> str:
    """A name from the operation and the names of its inputs. It always has a space, so it is never an identifier."""
    verb = step.op.split()[-1]
    first, *rest = [names.get(t.op(), "table") for t in step.inputs]
    other = rest[0] if rest else "table"
    how = step.kwargs.get("how") or (step.args[2] if verb == "join" and len(step.args) > 2 else "inner")
    count = step.args[0] if step.args else step.kwargs.get("n")
    if verb == "join" and how in ("semi", "anti"):
        name = f"{first} {'in' if how == 'semi' else 'not in'} {other}"
    elif verb in _SYMBOLS:
        name = f"{first} {_SYMBOLS[verb]} {other}"
    elif verb in ("limit", "head"):
        name = f"first {count} of {first}" if isinstance(count, int) else f"first rows of {first}"
    elif verb in ("aggregate", "agg"):
        name = f"{_few(step.created or [first])} by {_few(step.group_by)}" if step.group_by else f"{first} totals"
    elif verb == "drop":
        name = f"{first} without {_few([c for c in step.inputs[0].columns if c not in step.output.columns])}"
    elif verb in ("mutate", "select", "column") and step.created:
        name = f"{first}, {_few(step.created)}" if " with " in first else f"{first} with {_few(step.created)}"
    elif verb == "select":
        name = f"{first} ({_few(list(step.output.columns), 3)})"
    else:
        name = _PHRASES.get(verb, "{} after " + verb).format(first)
    return name if len(name) <= 40 else name[:39] + "…"  # ponytail: a cut, not a shorter phrase


def chains(steps, names: dict) -> list[list[int]]:
    """Runs of steps where each step reads the unnamed result of the step before, as in `t.a().b().c()`."""
    last_use = {t.op(): i for i, step in enumerate(steps) for t in step.inputs}
    runs = []
    for i, step in enumerate(steps):
        prev = steps[i - 1] if i else None
        linked = (prev is not None and step.inputs[0].op() == prev.output.op() and step.function == prev.function
                  and not names.get(prev.output.op(), "").isidentifier() and last_use.get(prev.output.op()) == i)
        if linked:
            runs[-1].append(i)
        else:
            runs.append([i])
    return runs


def plan(steps, keep_idle: int = 2) -> list[ScopeFrame]:
    last_use = {}
    for i, step in enumerate(steps):
        for table in step.inputs:
            last_use[table.op()] = i
    final = steps[-1].output.op() if steps else None
    panel, idle_since, frames = [], {}, []
    for i, step in enumerate(steps):
        inputs = list(dict.fromkeys(t.op() for t in step.inputs))
        panel += [k for k in inputs if k not in panel]  # a table appears when a step first reads it
        shown = list(panel)
        dead = {k for k in shown if last_use.get(k, -1) < i}
        out, first = step.output.op(), inputs[0]
        replaced = first if last_use[first] == i and out not in panel else None
        if replaced is not None:
            panel[panel.index(first)] = out
        elif out not in panel:
            panel.append(out)
        idle_since |= {k: i for k in [*inputs, out]}
        expired = [k for k in panel if k not in (out, final) and last_use.get(k, -1) <= i and i - idle_since[k] >= keep_idle]
        panel = [k for k in panel if k not in expired]
        dead_after = {k for k in panel if last_use.get(k, -1) <= i and k != final}
        frames.append(ScopeFrame(shown, inputs, out, list(panel), replaced, expired, dead, dead_after))
    return frames
