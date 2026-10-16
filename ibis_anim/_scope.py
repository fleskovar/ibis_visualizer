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


def table_names(steps) -> dict:
    """A name for each table: the first variable that holds it, else the operation that made it."""
    names = {}
    for step in steps:
        for table, label in zip(step.inputs, step.labels):
            if label.isidentifier() and label != "table":
                names.setdefault(table.op(), label)
    for step in steps:
        for table, label in zip(step.inputs, step.labels):
            names.setdefault(table.op(), label)
        names.setdefault(step.output.op(), f"{step.op.split()[-1]} result")
    if steps:
        names.setdefault(steps[-1].output.op(), "result")
    return names


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
