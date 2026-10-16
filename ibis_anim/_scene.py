"""Animate traced ibis steps as a manim video, one section per step.

Each step runs again on the first rows of its inputs, so each value on the screen comes from the
rows on the screen. Each kind of operation has its own choreography, in phases with a caption.
"""

from __future__ import annotations

import functools
import linecache
import os
import re
import sys
import textwrap
from dataclasses import dataclass, field, replace
from pathlib import Path

import ibis
import ibis.expr.operations as ops
import numpy as np
from manim import (
    BLACK, BLUE_D, BOLD, DOWN, GOLD_D, GREEN, GREEN_D, GREEN_E, GREY_A, GREY_B, GREY_C, ITALIC, LEFT, MAROON_C, NORMAL,
    ORANGE, ORIGIN, PI, PINK, PURPLE_B, RED, RED_E, RIGHT, TEAL_D, UL, UP, UR, WHITE, YELLOW, YELLOW_A, YELLOW_E,
    GREY_D, AnimationGroup, Arrow, Group, Circumscribe, DashedLine, FadeIn, FadeOut, FadeTransform, Indicate, LaggedStart, Line,
    Rectangle, ReplacementTransform, Rotate, RoundedRectangle, Scene, SurroundingRectangle, Text, Transform, TransformFromCopy, VectorizedPoint, VGroup,
    tempconfig,
)

from ibis_anim._derive import column_text, derive
from ibis_anim._scope import ScopeFrame, chains, plan, table_names
from ibis_anim._tracer import Step, _names, recording, trace

MONO = "Consolas" if sys.platform == "win32" else "Monospace"
SANS = "Segoe UI" if sys.platform == "win32" else "Sans"
PREVIEW_ROWS = 6
MAX_COLUMNS = 6
MAX_RESULT_ROWS = 8
ROW_H = 0.4
FACTS_H = 0.5
BAR_BOTTOM = 3.08  # top bar: step, call path and the current statement
CODE_Y = 3.3
STRIP_Y, CAPTION_Y = 2.72, 2.32  # the pipeline of the statement, then the caption of the phase
ZONE_TOP, ZONE_BOTTOM = 1.95, -2.05  # the stage
DIVIDER_X = -3.95  # left of it: the trace; right of it: the stage
STAGE_X, STAGE_W = 1.6, 10.6
TRACE_LINES = 14
SHELF_LINE, SHELF_Y, SHELF_LEFT, SHELF_RIGHT = -2.17, -3.28, -6.95, 6.95  # the tables in scope
THUMB_W, THUMB_H = 2.3, 1.3
CARD_FILL = "#1c1c1c"
CURRENT_LINE = "#3a3a14"
NEUTRAL = "#3a3a3a"
TYPE_FILL = "#1f2a38"
FACTS_FILL, FACTS_NEW, FACTS_TEXT = "#123029", "#1f5c4a", "#a8f0d4"  # the facts row; brighter when the step changed it
WARNING_FILL = "#3d1515"
CAPTION = "#8fd3ff"
PALETTE = [BLUE_D, ORANGE, PURPLE_B, GREEN_D, TEAL_D, MAROON_C, GOLD_D, PINK]


def visualize(func, *args, **kwargs) -> Path:
    """Trace `func(*args, **kwargs)` and render its steps."""
    _, steps = trace(func, *args, **kwargs)
    return render(steps, func.__name__)


def render(steps: list[Step], name: str, output_dir="media", quality="low_quality") -> Path:
    """Render one video with one section per step, and return the path of the video."""
    config = {"quality": quality, "media_dir": str(output_dir), "save_sections": True,
              "output_file": name, "verbosity": "WARNING", "progress_bar": "none"}
    with recording(None), tempconfig(config):  # the previews run ibis methods: do not record them
        scene = DebuggerScene(steps, title=name)
        scene.render()
        return Path(scene.renderer.file_writer.movie_file_path)


# ---------------------------------------------------------------------------------------------
# Data: samples, the step run again on the samples, and row matching


@dataclass
class Sample:
    """Rows of a table, as a grid shows them."""

    label: str
    columns: list[str]
    rows: list[dict]
    total: int
    partial: bool = False  # True when the rows come from a part of the input
    types: dict = field(default_factory=dict)  # column name → ibis type, for example "int64"
    source: object = None  # the op of the input table, to find its card on the panel
    facts: dict = field(default_factory=dict)  # column name → derived facts, for example 'unique · not null'
    changed: frozenset = frozenset()  # columns whose facts this step changed


def _count(table) -> int:
    return int(table.count().execute())


def _types(table) -> dict[str, str]:
    return {name: str(dtype).lstrip("!") for name, dtype in table.schema().items()}


def _run_on_samples(step: Step, given: dict | None = None):
    """The first rows of each input, the output of the step on these rows only, and the output rows as arrow.

    `given` maps an input op to (arrow rows, partial): in a chain, a step reads the rows that the step
    before it showed, not a new sample of the full table.
    """
    given = given or {}
    arrows = [given[t.op()][0] if t.op() in given else t.head(PREVIEW_ROWS).to_pyarrow() for t in step.inputs]
    mapping = {}
    for t, a in zip(step.inputs, arrows):
        mem = ibis.memtable(a)
        mapping[t.op()] = (mem if mem.schema() == t.schema() else mem.cast(t.schema())).op()
    ins = [Sample(label, t.columns, a.to_pylist(), _count(t), partial=t.op() in given and given[t.op()][1],
                  types=_types(t), source=t.op())
           for t, label, a in zip(step.inputs, step.labels, arrows)]
    result = step.output.op().replace(mapping).to_expr().to_pyarrow()
    partial = any(s.partial or len(s.rows) < s.total for s in ins)
    out = Sample("result", step.output.columns, result.to_pylist(), _count(step.output), partial, _types(step.output))
    return ins, out, result.slice(0, MAX_RESULT_ROWS)


def _plain_samples(step: Step) -> tuple[list[Sample], Sample]:
    ins = [Sample(label, t.columns, t.head(PREVIEW_ROWS).to_pyarrow().to_pylist(), _count(t), types=_types(t), source=t.op())
           for t, label in zip(step.inputs, step.labels)]
    out = step.output
    return ins, Sample("result", out.columns, out.head(PREVIEW_ROWS).to_pyarrow().to_pylist(), _count(out), types=_types(out))


def _match(outs: list[dict], ins: list[dict], pairs, reuse=False) -> list[int | None]:
    """For each output row, the input row with equal values on `pairs` of (output, input) columns."""
    taken, found = set(), []
    for o in outs:
        i = next((i for i, r in enumerate(ins) if (reuse or i not in taken) and all(o[a] == r[b] for a, b in pairs)), None)
        taken.add(i)
        found.append(i)
    return found


def _in_input_order(sample: Sample, idx: list) -> tuple[Sample, list]:
    """Sort the rows of `sample` by the index of their input row."""
    order = sorted(range(len(idx)), key=lambda k: (idx[k] is None, idx[k] or 0))
    return replace(sample, rows=[sample.rows[k] for k in order]), [idx[k] for k in order]


def _sort_perm(rows: list[dict], keys) -> list[int]:
    """Row order for `keys` of (column, ascending). Nulls go last."""
    perm = list(range(len(rows)))
    for name, ascending in reversed(keys):
        perm.sort(key=lambda i: (rows[i][name] is not None, rows[i][name]) if not ascending else
                  (rows[i][name] is None, rows[i][name] if rows[i][name] is not None else 0), reverse=not ascending)
    return perm


def _first_seen(rows: list[dict], keys) -> dict:
    order = {}
    for r in rows:
        order.setdefault(_hashable(tuple(r[k] for k in keys)), len(order))
    return order


def _sources(step: Step) -> dict[str, list[str]]:
    """For each output column that the step computes, the input columns that it reads."""
    op = step.output.op()
    exprs = {**getattr(op, "values", {}), **getattr(op, "metrics", {})}
    return {name: list(dict.fromkeys(f.name for f in v.find(ops.Field, filter=ops.Value))) for name, v in exprs.items()}


_INFIX = {
    ops.Add: "+", ops.Subtract: "-", ops.Multiply: "*", ops.Divide: "/", ops.FloorDivide: "//", ops.Modulus: "%",
    ops.Power: "**", ops.Equals: "==", ops.NotEquals: "!=", ops.Greater: ">", ops.GreaterEqual: ">=", ops.Less: "<",
    ops.LessEqual: "<=", ops.And: "&", ops.Or: "|",
}


def _expr_text(op) -> str:
    """A short formula for a value expression, for example `cast(score * 10 as int64)`."""
    match op:
        case _ if not isinstance(op, ops.Node):
            return repr(op)
        case ops.Field():
            return op.name
        case ops.Literal():
            return repr(op.value)
        case ops.Cast():
            return f"cast({_expr_text(op.arg)} as {op.to})"
        case ops.WindowFunction():
            return _expr_text(op.func)
        case _ if type(op) in _INFIX:
            side = lambda x: f"({_expr_text(x)})" if type(x) in _INFIX else _expr_text(x)  # noqa: E731
            return f"{side(op.left)} {_INFIX[type(op)]} {side(op.right)}"
    args = [a for arg in op.args for a in (arg if isinstance(arg, tuple) else (arg,)) if isinstance(a, ops.Value)]
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", type(op).__name__).lower()
    return f"{name}({', '.join(map(_expr_text, args))})"


def _formula(step: Step, name: str) -> str:
    op = step.output.op()
    exprs = {**getattr(op, "values", {}), **getattr(op, "metrics", {})}
    return f"{name} = {_expr_text(exprs[name])}" if name in exprs else name


def _call_args(code: str, verb: str) -> str:
    i = code.find(f"{verb}(")
    return code[i + len(verb) + 1:-1] if i >= 0 and code.endswith(")") else code


def _window(step: Step):
    """(column, window function) for the first computed column that is a window function."""
    values = getattr(step.output.op(), "values", {})
    return next(((name, w) for name in step.created if name in values
                 for w in values[name].find(ops.WindowFunction, filter=ops.Value)), None)


def _offset(boundary) -> int:
    return -boundary.value.value if boundary.preceding else boundary.value.value


def _frame_text(w) -> str:
    def side(boundary, default):
        if boundary is None:
            return default
        n = _offset(boundary)
        return "the current row" if n == 0 else f"{abs(n)} rows {'before' if n < 0 else 'after'}"

    return f"from {side(w.start, 'the first row of the group')} to {side(w.end, 'the last row of the group')}"


# ---------------------------------------------------------------------------------------------
# Drawing: cells and grids


def _fmt(value) -> str:
    if value is None:
        return "null"
    text = f"{value:,.2f}" if isinstance(value, float) else str(value)
    return text if len(text) <= 12 else text[:11] + "…"


@functools.cache
def _char_width() -> float:
    return Text("0" * 20, font=MONO, font_size=19, weight=BOLD).width / 20


def _cell_width(samples: list[Sample], columns) -> float:
    texts = [*columns, *(_fmt(r[c]) for s in samples for r in s.rows for c in columns if c in r),
             *(s.types.get(c, "") for s in samples for c in columns)]
    return min(max(map(len, texts)) * _char_width() + 0.3, 2.2)


def _cell(text: str, width: float, fill, opacity: float, header=False, dark=False, italic=False) -> VGroup:
    box = Rectangle(width=width, height=ROW_H, stroke_width=1, stroke_color=GREY_B).set_fill(fill, opacity)
    label = Text(text, font=MONO, font_size=19 if header else 17, weight=BOLD if header else NORMAL, slant=ITALIC if italic else NORMAL,
                 color=BLACK if dark else WHITE)
    if label.width > width - 0.12:
        label.scale_to_fit_width(width - 0.12)
    return VGroup(box, label.move_to(box))


def _facts_cell(text: str, width: float, changed: bool) -> VGroup:
    """Two short lines: the flags (unique, not null), then the value set or the lineage."""
    box = Rectangle(width=width, height=FACTS_H, stroke_width=1, stroke_color=GREY_B).set_fill(FACTS_NEW if changed else FACTS_FILL, 1)
    parts = [p for p in text.split(" · ") if p]
    flags = [p for p in parts if p in ("unique", "not null", "always null")]
    rest = [p for p in parts if p not in flags]
    lines = [" · ".join(flags)] if flags else []
    lines += (rest or ["any"])[: 2 - len(lines)]  # no value set: any value of the type
    label = Text("\n".join(lines), font=SANS, font_size=12, color=GREY_C if lines == ["any"] else FACTS_TEXT,
                 line_spacing=0.7)  # Consolas lacks ⊆ ∪ ∅
    label.scale(min(1, (width - 0.08) / label.width, FACTS_H * 0.85 / label.height))
    return VGroup(box, label.move_to(box))


def _hashable(key: tuple) -> tuple:
    return tuple(tuple(v) if isinstance(v, list) else v for v in key)


def _color(colors: dict, key):
    return colors.setdefault(_hashable(key), PALETTE[len(colors) % len(PALETTE)])


def _pick_columns(columns, important, limit=MAX_COLUMNS) -> list[str]:
    keep = list(dict.fromkeys(c for c in important if c in columns))[:limit]
    keep += [c for c in columns if c not in keep][: limit - len(keep)]
    return [c for c in columns if c in keep]


class Grid(VGroup):
    """A table drawn as cells of equal size. Row -1 is the header row."""

    def __init__(self, sample: Sample, shown, width: float, used=(), created=(), keys=(), colors=None, types=True):
        super().__init__()
        self.sample, self.shown, self.cells = sample, list(shown), {}
        for j, name in enumerate(self.shown):
            marked = name in used or name in created
            fill = YELLOW_E if name in used else GREEN_E if name in created else NEUTRAL
            x = (j + 0.5) * width
            self.cells[-1, name] = _cell(name, width, fill, 1, header=True, dark=marked).move_to([x, 0, 0])
            if types:  # a type row above the header, as row -2
                self.cells[-2, name] = _cell(sample.types.get(name, "?"), width, GREEN_E if name in created else TYPE_FILL,
                                             1, dark=name in created, italic=True).move_to([x, ROW_H, 0])
            if sample.facts:  # a facts row above the type row, as row -3
                self.cells[-3, name] = _facts_cell(sample.facts.get(name, ""), width, name in sample.changed).move_to(
                    [x, ROW_H * 1.5 + FACTS_H / 2, 0])
            for r, row in enumerate(sample.rows):
                cell = _cell(_fmt(row.get(name)), width, fill, 0.18 if marked else 0)
                self.cells[r, name] = cell.move_to([x, -(r + 1) * ROW_H, 0])
        if keys and colors is not None:
            for r, row in enumerate(sample.rows):
                for name in self.shown:
                    self.cells[r, name][0].set_fill(_color(colors, tuple(row.get(k) for k in keys)), 0.45)
        self.anchors = VGroup(VectorizedPoint([0, ROW_H / 2, 0]), VectorizedPoint([width, -ROW_H / 2, 0]))
        shown_rows = len(sample.rows)
        if sample.partial:
            caption = f"{sample.label} of the rows shown · {sample.total} rows in full"
        elif shown_rows < sample.total:
            caption = f"{sample.label} · first {shown_rows} of {sample.total} rows"
        else:
            caption = f"{sample.label} · {sample.total} rows"
        self.title = Text(caption, font=SANS, font_size=18, color=GREY_A)
        self.title.next_to(VGroup(*self.cells.values()), UP, buff=0.12, aligned_edge=LEFT)
        self.add(*self.cells.values(), self.anchors, self.title)

    def slot(self, r: int, j: int) -> np.ndarray:
        """Centre of the cell at row `r` and column position `j`, also for a cell that does not exist."""
        a, b = (p.get_center() for p in self.anchors)
        return np.array([a[0] + (j + 0.5) * (b[0] - a[0]), a[1] - (r + 1.5) * (a[1] - b[1]), 0])

    @property
    def cell_w(self) -> float:
        return self.anchors[1].get_center()[0] - self.anchors[0].get_center()[0]

    def cell(self, r: int, name: str) -> VGroup:
        return self.cells[r, name]

    def header(self, name: str) -> VGroup:
        return self.cells[-1, name]

    def row(self, r: int) -> VGroup:
        return VGroup(*[self.cells[r, n] for n in self.shown])

    def type_cell(self, name: str) -> VGroup:
        return self.cells[-2, name]

    def column(self, name: str, header=True) -> VGroup:
        rows = range(-3 if header else 0, len(self.sample.rows))
        return VGroup(*[self.cells[r, name] for r in rows if (r, name) in self.cells])

    def commit_rows(self, perm: list[int]):
        """Renumber the rows after row perm[j] moved to position j."""
        moved = {(j, n): self.cells[i, n] for j, i in enumerate(perm) for n in self.shown}
        self.cells.update(moved)
        self.sample = replace(self.sample, rows=[self.sample.rows[i] for i in perm])


# ---------------------------------------------------------------------------------------------
# The scene


class Card(VGroup):
    """One table on the tables shelf: name, shape, and a mini table of its first rows."""

    def __init__(self, name: str, table, rows: int, preview: list[dict], key: str = ""):
        super().__init__()
        self.box = RoundedRectangle(corner_radius=0.07, width=THUMB_W, height=THUMB_H, stroke_width=1.2, stroke_color=GREY_B)
        self.box.set_fill(CARD_FILL, 1)
        title = Text(name, font=SANS, font_size=16, weight=BOLD)
        if title.width > THUMB_W * 0.62:
            title.scale_to_fit_width(THUMB_W * 0.62)
        shape = Text(f"{rows} × {len(table.columns)}", font=MONO, font_size=13, color=GREY_A)
        title.move_to(self.box.get_corner(UL) + [0.1 + title.width / 2, -0.17, 0])
        shape.move_to(self.box.get_corner(UR) + [-0.1 - shape.width / 2, -0.17, 0])
        columns = table.columns[:4]
        cw, rh = (THUMB_W - 0.16) / max(len(columns), 1), 0.22
        top_left = self.box.get_corner(UL) + [0.08, -0.36, 0]
        mini = VGroup()
        for j, name_ in enumerate(columns):
            for r in range(-1, min(3, len(preview))):
                text = name_ if r < 0 else _fmt(preview[r].get(name_))
                cell = Rectangle(width=cw, height=rh, stroke_width=0.6, stroke_color=GREY_D)
                cell.set_fill(NEUTRAL if r < 0 else CARD_FILL, 1)
                label = Text(text[:12], font=MONO, font_size=11, color=WHITE if r < 0 else GREY_A)
                label.scale(min(1, (cw - 0.05) / label.width, rh * 0.75 / label.height))
                cell.move_to(top_left + [(j + 0.5) * cw, -(r + 1.5) * rh, 0])
                mini.add(VGroup(cell, label.move_to(cell)))
        more = len(table.columns) - len(columns)
        self.add(self.box, title, shape, mini)
        footer = " · ".join(t for t in (f"key: {key}" if key else "", f"+{more} more columns" if more else "") if t)
        if footer:
            line = Text(footer, font=SANS, font_size=11, color=FACTS_TEXT if key else GREY_B)
            if line.width > THUMB_W - 0.16:
                line.scale_to_fit_width(THUMB_W - 0.16)
            self.add(line.move_to(self.box.get_bottom() + [0, 0.12, 0]))

    def set_state(self, state: str):
        """input: read by the step; output: made by the step; idle: in scope; dead: no later step reads it."""
        color, width, opacity = {"input": (YELLOW, 3, 1), "output": (GREEN, 3, 1), "idle": (GREY_B, 1.2, 0.85),
                                 "dead": (GREY_C, 1, 0.35)}[state]
        self.set_opacity(opacity)
        self.box.set_stroke(color, width, opacity)
        return self


CARD_SCALE = {"input": 1.15, "output": 1.15, "idle": 1.0, "dead": 0.72}  # active tables are drawn larger


def _card_slots(states: list[str]) -> list[tuple[np.ndarray, float]]:
    """Centre and width of each card on the shelf, left to right. Many cards get smaller."""
    widths = [THUMB_W * CARD_SCALE[s] for s in states]
    gap = 0.15
    fit = min(1.0, (SHELF_RIGHT - SHELF_LEFT - gap * len(widths)) / max(sum(widths), 0.01))
    slots, x = [], SHELF_LEFT
    for w in widths:
        slots.append((np.array([x + w * fit / 2, SHELF_Y, 0]), w * fit))
        x += w * fit + gap
    return slots


def _chip_text(step: Step) -> str:
    """The call of a step without its receiver: `mutate(line_total=…)`, shortened."""
    code = " ".join(step.code.split())
    dot = code.find(".")
    if dot > 0 and code[:dot].isidentifier():
        code = code[dot + 1:]
    return code if len(code) <= 34 else code[:32] + "…)"


class DebuggerScene(Scene):
    """A debugger view: the trace on the left, the current statement on top, the step in the middle,
    and the tables in scope on a shelf at the bottom."""

    def __init__(self, steps: list[Step], title: str, **kwargs):
        self.steps, self.title = steps, title
        self.frames, self.names = plan(steps), table_names(steps)
        try:
            self.facts = derive(steps, self.names)
        except Exception as exc:  # the video works without facts
            print(f"ibis_anim: no facts ({exc!r})", file=sys.stderr)
            self.facts = {}
        self.chain_of = {i: run for run in chains(steps, self.names) for i in run}
        self.chrome = self.caption = self.trace_panel = self.result = self.carry = self.carry_op = None
        self.chain_rows, self.next_rows = {}, {}  # the rows that a chain hands from one step to the next
        self.cards, self.rows, self.previews = {}, {}, {}
        self.zone_top = ZONE_TOP
        self.trace_lines = self._trace_lines()
        self.frame = VGroup(
            Line([-7.1, BAR_BOTTOM, 0], [7.1, BAR_BOTTOM, 0], stroke_width=1, color=GREY_D),
            Line([DIVIDER_X, BAR_BOTTOM, 0], [DIVIDER_X, SHELF_LINE, 0], stroke_width=1, color=GREY_D),
            Line([-7.1, SHELF_LINE, 0], [7.1, SHELF_LINE, 0], stroke_width=1, color=GREY_D),
            Text("TRACE", font=SANS, font_size=14, weight=BOLD, color=GREY_B).move_to([SHELF_LEFT + 0.3, BAR_BOTTOM - 0.18, 0]),
            Text("TABLES IN SCOPE", font=SANS, font_size=13, weight=BOLD, color=GREY_B)
            .move_to([SHELF_LEFT + 0.75, SHELF_LINE - 0.15, 0]),
        )
        super().__init__(**kwargs)

    def construct(self):
        self.next_section("00_overview")
        self._overview()
        self.play(FadeIn(self.frame))
        for i, (step, frame) in enumerate(zip(self.steps, self.frames)):
            self.next_section(f"{i + 1:02d}_{step.function}_{step.op.split()[-1]}")
            self._header(i, step)
            self._activate(frame)
            self.result = None
            self._animate(step)
            self._guarantees(step)
            self.wait(1.5)
            self._finish(i, step, frame)
        self.wait(2)

    def _overview(self):
        title = Text(f"Tracing {self.title}", font=SANS, font_size=40, weight=BOLD).to_edge(UP)
        lines = [f"{i:>2}  {'  ' * (len(s.stack) - 1)}{s.function} › {s.op}" for i, s in enumerate(self.steps, 1)]
        listing = Text("\n".join(lines) or "no ibis operations", font=MONO, font_size=22, line_spacing=0.8)
        listing.next_to(title, DOWN, buff=0.5)
        if listing.height > 6:
            listing.scale_to_fit_height(6)
        self.play(FadeIn(title), FadeIn(listing, shift=UP * 0.2))
        self.wait(2)
        self.play(FadeOut(title), FadeOut(listing))

    # -- the debugger frame ------------------------------------------------------------------

    def _trace_lines(self) -> list[tuple[str, int, str, int]]:
        """(kind, step, text, depth): one line per function call, and one line per step under it."""
        lines, previous = [], None
        for i, step in enumerate(self.steps):
            calls = tuple(f"{f.name}()" for f in step.stack if f.name != "<module>")
            calls = calls or (os.path.basename(step.stack[-1].filename) if step.stack else "?",)  # module-level code: the script
            if calls != previous:
                lines.append(("call", i, calls[-1], len(calls) - 1))
                previous = calls
            lines.append(("step", i, f"{i + 1:>2} {step.op}", len(calls) - 1))
        return lines

    def _trace(self, i: int) -> VGroup:
        """The trace around step i: the steps that ran, the current step, and the steps to come."""
        current = next(k for k, (kind, s, _, _) in enumerate(self.trace_lines) if kind == "step" and s == i)
        start = max(0, min(current - 5, len(self.trace_lines) - TRACE_LINES))
        panel = VGroup()
        for k, (kind, s, text, depth) in enumerate(self.trace_lines[start:start + TRACE_LINES]):
            y = BAR_BOTTOM - 0.48 - k * 0.33
            indent = SHELF_LEFT + 0.12 + depth * 0.18
            if kind == "call":
                inside = s <= i and all(not (kk == "call" and i >= ss > s) for kk, ss, _, _ in self.trace_lines)
                line = Text(text, font=MONO, font_size=13, weight=BOLD, color=WHITE if inside else GREY_C)
                panel.add(line.move_to([indent + line.width / 2, y, 0]))
                continue
            done, now = s < i, s == i
            icon = Text("✔" if done else "▸" if now else "·", font=SANS, font_size=13,
                        color=GREEN if done else YELLOW if now else GREY_D)
            warns = bool(self.facts.get(self.steps[s].output.op()) and self.facts[self.steps[s].output.op()].warnings)
            line = Text(text, font=MONO, font_size=13, color=GREY_B if done else YELLOW_A if now else GREY_C)
            icon.move_to([indent + 0.3, y, 0])
            line.move_to([indent + 0.45 + line.width / 2, y, 0])
            if line.get_right()[0] > DIVIDER_X - 0.1:
                line.scale_to_fit_width(DIVIDER_X - 0.1 - line.get_left()[0], about_edge=LEFT)
            row = VGroup(icon, line)
            if warns:  # known before the step runs: the analysis covers the whole trace
                row.add(Text("⚠", font=SANS, font_size=13, color=RED).next_to(line, RIGHT, buff=0.08))
            if now:
                band = Rectangle(width=DIVIDER_X - SHELF_LEFT + 0.1, height=0.3, stroke_width=0).set_fill(CURRENT_LINE, 1)
                row = VGroup(band.move_to([(SHELF_LEFT + DIVIDER_X) / 2, y, 0]), *row)
            panel.add(row)
        if start > 0:
            panel.add(Text("⋮", font=SANS, font_size=13, color=GREY_C).move_to([SHELF_LEFT + 2.6, BAR_BOTTOM - 0.18, 0]))
        return panel

    def _strip(self, i: int) -> VGroup:
        """The statement as a pipeline: source → each call of the chain → result, with the current call lit."""
        run = self.chain_of[i]
        source = self.names.get(self.steps[run[0]].inputs[0].op(), "table")
        result = self.names.get(self.steps[run[-1]].output.op(), "result")
        items = [Text(source, font=SANS, font_size=16, weight=BOLD, color=GREY_A)]
        for k in run:
            done, now = k < i, k == i
            text = Text(_chip_text(self.steps[k]), font=MONO, font_size=14, color=GREY_B if done else YELLOW_A if now else GREY_C)
            box = RoundedRectangle(corner_radius=0.08, width=text.width + 0.24, height=text.height + 0.2,
                                   stroke_width=2.5 if now else 1, stroke_color=YELLOW if now else GREY_D)
            box.set_fill(CURRENT_LINE if now else CARD_FILL, 1)
            items.append(VGroup(box, text.move_to(box)))
        items.append(Text(result, font=SANS, font_size=16, weight=BOLD, color=GREEN if i == run[-1] else GREY_C))
        strip = VGroup()
        for k, item in enumerate(items):
            if k:
                strip.add(Text("→", font=MONO, font_size=16, color=GREY_B))
            strip.add(item)
        strip.arrange(RIGHT, buff=0.12)
        if strip.width > STAGE_W:
            strip.scale_to_fit_width(STAGE_W)
        return strip.move_to([DIVIDER_X + 0.25 + strip.width / 2, STRIP_Y, 0])

    def _header(self, i: int, step: Step):
        """Top bar: step, operation, call path and the current statement. Left: the trace. Middle: the pipeline."""
        frame = step.stack[-1] if step.stack else None
        op = Text(step.op.upper(), font=SANS, font_size=26, weight=BOLD)
        op.move_to([SHELF_LEFT + op.width / 2, 3.72, 0])
        calls = " › ".join(f.name for f in step.stack if f.name != "<module>")
        path = Text(f"{calls}()" if calls else "", font=MONO, font_size=15, color=GREY_A)
        path.next_to(op, RIGHT, buff=0.35, aligned_edge=DOWN)
        counter = Text(f"step {i + 1}/{len(self.steps)}", font=SANS, font_size=17, color=GREY_B).to_corner(UR, buff=0.25)
        statement = linecache.getline(frame.filename, frame.lineno).strip() if frame else ""
        code = Text(f"{frame.lineno if frame else '':>4}  {(statement or step.code)[:130]}", font=MONO, font_size=15, color=YELLOW_A)
        where = Text(f"{os.path.basename(frame.filename)}:{frame.lineno}" if frame else "", font=MONO, font_size=13, color=GREY_C)
        where.move_to([7.0 - where.width / 2, CODE_Y, 0])
        if code.width > where.get_left()[0] - SHELF_LEFT - 0.3:
            code.scale_to_fit_width(where.get_left()[0] - SHELF_LEFT - 0.3)
        code.move_to([SHELF_LEFT + code.width / 2, CODE_Y, 0])
        band = Rectangle(width=14.2, height=0.36, stroke_width=0).set_fill(CURRENT_LINE, 1).move_to([0, CODE_Y, 0])
        chrome = VGroup(band, op, path, counter, code, where, self._strip(i))
        trace = self._trace(i)
        anims = [FadeTransform(self.chrome, chrome) if self.chrome else FadeIn(chrome)]
        anims.append(FadeTransform(self.trace_panel, trace) if self.trace_panel else FadeIn(trace))
        self.play(*anims, run_time=0.6)
        self.chrome, self.trace_panel = chrome, trace

    def _card(self, key) -> Card:
        if key not in self.rows:
            table = key.to_expr()
            self.rows[key] = _count(table)
            self.previews[key] = table.head(3).to_pyarrow().to_pylist()
        facts = self.facts.get(key)
        first_key = ", ".join(sorted(min(facts.keys, key=len))) if facts and facts.keys else ""
        return Card(self.names.get(key, "table"), key.to_expr(), self.rows[key], self.previews[key], first_key)

    def _activate(self, frame: ScopeFrame):
        """New tables join the shelf; the step's inputs light up and grow; the others dim."""
        states = ["input" if k in frame.inputs else "dead" if k in frame.dead else "idle" for k in frame.shown]
        anims = []
        for key, state, (pos, width) in zip(frame.shown, states, _card_slots(states)):
            if key not in self.cards:
                self.cards[key] = card = self._card(key).set_state(state)
                anims.append(FadeIn(card.scale_to_fit_width(width).move_to(pos), shift=UP * 0.3))
            else:
                anims.append(self.cards[key].animate.set_state(state).scale_to_fit_width(width).move_to(pos))
        self.play(*anims, run_time=0.7)
        self.play(*[Circumscribe(self.cards[k], color=YELLOW, buff=0.04) for k in frame.inputs], run_time=0.8)

    def _facts_texts(self, op) -> dict:
        facts = self.facts.get(op)
        return {c: column_text(facts, c, self.names.get(op, "")) for c in facts.columns} if facts else {}

    def _with_facts(self, step: Step, ins: list[Sample], out: Sample) -> tuple[list[Sample], Sample]:
        inputs = [self.names.get(t.op(), "") for t in step.inputs]
        for sample, table in zip(ins, step.inputs):
            sample.facts = self._facts_texts(table.op())
        out.facts = self._facts_texts(step.output.op())
        before = ins[0].facts if ins else {}
        out.changed = frozenset(c for c, text in out.facts.items() if text and text != before.get(c, ""))
        return ins, out

    def _guarantees(self, step: Step):
        """What the step guarantees about its result, then its warnings."""
        facts = self.facts.get(step.output.op())
        if facts is None:
            return
        after, before = self._facts_texts(step.output.op()), self._facts_texts(step.inputs[0].op())
        changed = [c for c in step.output.columns if after.get(c) and after[c] != before.get(c, "")]  # lineage included
        items = [f"{c}: {after[c]}" for c in changed[:3]]
        if facts.rows and facts.rows != "?":
            items.append(f"rows {facts.rows}")
        if facts.can_empty:  # "not null" says that no row has a null, not that there is a row
            items.append("the result can be empty")
        if items:
            self._say("guaranteed: " + "; ".join(items), wait=0.3)
            grid = self.result if isinstance(self.result, Grid) else None
            cells = [grid.cells[-3, c] for c in changed if grid is not None and (-3, c) in grid.cells]
            anims = [Circumscribe(c, color=FACTS_TEXT, buff=0.03) for c in cells]
            if facts.can_empty and grid is not None:
                tag = Text("can be empty", font=SANS, font_size=15, color=ORANGE)
                tag.scale(grid.title.height / Text("result", font=SANS, font_size=18).height)
                anims.append(FadeIn(tag.next_to(grid.title, RIGHT, buff=0.25), shift=LEFT * 0.2))
            if anims:
                self.play(*anims, run_time=1)
            self.wait(1)
        boxes = VGroup()
        for warning in facts.warnings[:2]:
            text = Text("⚠ " + textwrap.fill(warning, 85), font=SANS, font_size=16, color=WHITE, t2c={"⚠": RED})
            box = RoundedRectangle(corner_radius=0.08, width=text.width + 0.3, height=text.height + 0.2, stroke_color=RED,
                                   stroke_width=2).set_fill(WARNING_FILL, 0.95)
            boxes.add(VGroup(box, text.move_to(box)))
        if len(boxes):
            boxes.arrange(UP, buff=0.12, aligned_edge=LEFT)
            if boxes.width > STAGE_W:
                boxes.scale_to_fit_width(STAGE_W)
            boxes.move_to([DIVIDER_X + 0.25 + boxes.width / 2, ZONE_BOTTOM + 0.1 + boxes.height / 2, 0])
            self.play(LaggedStart(*[FadeIn(b, shift=UP * 0.2) for b in boxes], lag_ratio=0.3), run_time=0.8)
            self.wait(2.5)

    def _enter(self, *grids):
        """Each input table grows out of its card on the shelf. In a chain, the table already on the stage
        moves into place instead."""
        carried = [g for g in grids if self.carry is not None and g.sample.source == self.carry_op][:1]
        if carried:
            self._morph(self.carry, carried[0])
            self.carry = None
        anims = []
        for g in grids:
            if g not in carried:
                card = self.cards.get(g.sample.source)
                anims.append(FadeIn(g, target_position=card.get_center(), scale=0.2) if card else FadeIn(g))
        if anims:
            self.play(*anims, run_time=0.9)

    def _morph(self, old: Grid, new: Grid):
        """Hand a table from one layout to the next: each column and each row moves to its new place.

        A cell moves to the cell of the same column and the same row (rows are matched by their values).
        Cells of columns or rows that the new layout does not show leave first; new cells arrive last.
        """
        present = {id(m) for m in old.submobjects}  # rows that an in-place step removed are gone from the grid
        live = lambda key: key in old.cells and id(old.cells[key]) in present  # noqa: E731
        shared = [c for c in new.shown if c in old.shown]
        old_rows = [r for r in range(len(old.sample.rows)) if any(live((r, c)) for c in old.shown)]
        found = _match(new.sample.rows, [old.sample.rows[r] for r in old_rows], [(c, c) for c in shared])
        row_of = {k: old_rows[m] for k, m in enumerate(found) if m is not None}
        moves = [(old.cells[r, c], new.cells[r, c]) for c in shared for r in (-3, -2, -1) if live((r, c)) and (r, c) in new.cells]
        moves += [(old.cells[r, c], new.cells[k, c]) for c in shared for k, r in row_of.items() if live((r, c))]
        moved, targets = {id(src) for src, _ in moves}, {id(dst) for _, dst in moves}
        leaving = [cell for key, cell in old.cells.items() if live(key) and id(cell) not in moved]
        arriving = [cell for cell in new.cells.values() if id(cell) not in targets]
        if leaving:
            self.play(*[FadeOut(cell, shift=DOWN * 0.2) for cell in leaving], run_time=0.5)
        arc = lambda src, dst: 0.0 if abs(src.get_x() - dst.get_x()) < 0.05 else 0.7  # noqa: E731
        self.play(Transform(old.title, new.title.copy()),
                  *[Transform(src, dst.copy(), path_arc=arc(src, dst)) for src, dst in moves], run_time=1.5)
        self.remove(*old.get_family())
        self.add(*[m for m in new.submobjects if not any(m is cell for cell in arriving)])
        if arriving:
            self.play(*[FadeIn(cell) for cell in arriving], run_time=0.5)
        self.add(new)

    def _finish(self, i: int, step: Step, frame: ScopeFrame):
        """The result shrinks into its card; replaced and expired cards leave; the shelf closes up.

        When the next step continues the chain, the result stays on the stage for it, and its card
        appears on the shelf without the result moving there.
        """
        self.carry = None  # a carried table that the step did not read leaves with the stage
        continues = i + 1 < len(self.steps) and self.chain_of[i + 1] is self.chain_of[i] and self.result is not None
        persistent = {id(m) for m in self._persistent()}
        result = self.result or Group(*[m for m in self.mobjects if id(m) not in persistent])  # the stage can hold plain Groups
        family = {id(m) for m in result.get_family()}
        stage = [m for m in self.mobjects if id(m) not in persistent and not family & {id(x) for x in m.get_family()}]
        states = ["output" if k == frame.output else "dead" if k in frame.dead_after else "idle" for k in frame.after]
        slots = dict(zip(frame.after, _card_slots(states)))
        pos, width = slots[frame.output]
        card = self._card(frame.output).set_state("output").scale_to_fit_width(width).move_to(pos)
        anims = [FadeOut(m) for m in stage] + [FadeIn(card, scale=0.7)]
        if not continues:
            anims.append(FadeOut(result, target_position=pos, scale=0.12))
        if self.caption:
            anims.append(FadeOut(self.caption))
        for key in [frame.replaced, *frame.expired]:
            if key is not None and key in self.cards:
                anims.append(FadeOut(self.cards.pop(key), shift=DOWN * 0.4 if key != frame.replaced else ORIGIN))
        for key, state in zip(frame.after, states):
            if key in self.cards:
                p, w = slots[key]
                anims.append(self.cards[key].animate.set_state(state).scale_to_fit_width(w).move_to(p))
        self.play(*anims, run_time=1.3)
        self.cards[frame.output], self.caption = card, None
        if continues:
            self.carry, self.carry_op, self.chain_rows = result, frame.output, self.next_rows
        else:
            self.chain_rows = {}
        self._settle()

    def _persistent(self) -> list:
        return [m for m in (self.frame, self.trace_panel, self.chrome, self.caption, self.carry, *self.cards.values()) if m is not None]

    # -- helpers -----------------------------------------------------------------------------

    def _say(self, text: str, wait=0.6):
        """Show the caption of the current phase under the pipeline."""
        new = Text("▸ " + textwrap.fill(text, 95), font=SANS, font_size=18, color=CAPTION)
        if new.width > STAGE_W:
            new.scale_to_fit_width(STAGE_W)
        new.move_to([DIVIDER_X + 0.25 + new.width / 2, CAPTION_Y - new.height / 2 + 0.12, 0])
        self.play(FadeTransform(self.caption, new) if self.caption else FadeIn(new), run_time=0.5)
        self.caption = new
        if wait:
            self.wait(wait)

    def _settle(self, *keep):
        """Keep only the debugger frame, the caption and `keep` on the screen."""
        # manim splits a mobject into its parts when one part leaves the scene: keep the parts of what persists
        persistent = {id(x) for m in self._persistent() for x in m.get_family()}
        self.remove(*[m for m in self.mobjects if id(m) not in persistent])
        self.add(*[m for m in (self.carry,) if m is not None], *keep)

    def _scale(self, grids, width: float, height: float) -> float:
        f = min(1.5, STAGE_W / width, (self.zone_top - ZONE_BOTTOM) / height)  # small tables grow, to stay readable
        for g in grids:
            g.scale(f, about_point=ORIGIN)
        return f

    def _put(self, grid: Grid, left: float):
        grid.shift([left - grid.get_left()[0], self.zone_top - grid.get_top()[1], 0])

    def _side_by_side(self, grids, gap=1.2):
        width = sum(g.width for g in grids) + gap * (len(grids) - 1)
        f = self._scale(grids, width, max(g.height for g in grids))
        x = STAGE_X - width * f / 2
        for g in grids:
            self._put(g, x)
            x += g.width + gap * f

    def _in_place(self, first: Grid, *others: Grid):
        """Draw the grids on top of each other, with the same top-left cell."""
        grids = [first, *others]
        self._scale(grids, max(g.width for g in grids) + 2.5, max(g.height for g in grids))  # room for marks and arrows
        self._put(first, STAGE_X - max(g.width for g in grids) / 2)
        for g in others:
            g.shift(first.slot(-1, 0) - g.slot(-1, 0))

    def _highlight(self, grid: Grid, names, color=YELLOW):
        columns = [grid.column(n) for n in names if n in grid.shown]
        if columns:
            self.play(*[Circumscribe(c, color=color, buff=0.03) for c in columns], run_time=1.2)

    def _fly(self, groups, run_time=1.5, lag_ratio=0.0, path_arc=0.0):
        """Copies of the source cells move into the target cells. Each group of pairs moves together."""
        movers, anims = [], []
        for pairs in groups:
            copies = [src.copy() for src, _ in pairs]
            movers += copies
            anims.append(AnimationGroup(*[Transform(c, dst.copy(), path_arc=path_arc) for c, (_, dst) in zip(copies, pairs)]))
        if anims:
            self.play(LaggedStart(*anims, lag_ratio=lag_ratio), run_time=run_time)
        self.remove(*movers)
        self.add(*dict.fromkeys(dst for pairs in groups for _, dst in pairs))

    def _drop(self, grid: Grid, parts, shift=RIGHT * 0.6):
        """Fade `parts` out and take their cells out of `grid`. FadeOut alone restores the mobject at the end."""
        self.play(*[FadeOut(p, shift=shift) for p in parts], run_time=0.9)
        cells = [c for p in parts for c in p.submobjects]
        grid.remove(*cells)
        self.remove(*parts, *cells)

    def _tint(self, grid: Grid, keys, colors: dict) -> list:
        return [grid.cell(r, n)[0].animate.set_fill(_color(colors, tuple(row[k] for k in keys)), 0.45)
                for r, row in enumerate(grid.sample.rows) for n in grid.shown]

    def _reorder(self, grid: Grid, perm: list[int], run_time=2.5) -> bool:
        """Move row perm[j] to position j along an arc, so that the rows visibly change places."""
        if perm == sorted(perm):
            return False
        anims = [grid.row(i).animate(path_arc=1.6).shift(UP * (grid.slot(j, 0)[1] - grid.slot(i, 0)[1]))
                 for j, i in enumerate(perm) if i != j]  # up and down moves arc to opposite sides
        self.play(LaggedStart(*anims, lag_ratio=0.25), run_time=run_time + 0.3 * len(anims))
        grid.commit_rows(perm)
        return True

    def _sort_arrow(self, grid: Grid, keys) -> VGroup:
        """A vertical arrow right of the grid, with the sort keys and their direction."""
        top, bottom = grid.slot(0, len(grid.shown)), grid.slot(len(grid.sample.rows) - 1, len(grid.shown))
        x = top[0] - grid.cell_w / 2 + 0.35
        arrow = Arrow([x, top[1] + 0.15, 0], [x, bottom[1] - 0.15, 0], buff=0, color=YELLOW)
        text = "\n".join(f"{name}\n{'low → high' if asc else 'high → low'}" for name, asc in keys)
        label = Text(text, font=SANS, font_size=16, color=YELLOW).next_to(arrow, RIGHT, buff=0.12)
        return VGroup(arrow, label)

    # -- dispatch ----------------------------------------------------------------------------

    def _animate(self, step: Step):
        verb = step.op.split()[-1]
        handler = (
            self._filter if verb == "filter" else
            self._aggregate if verb in ("aggregate", "agg") else
            self._join if verb == "join" else
            self._pivot_wider if verb == "pivot_wider" else
            self._pivot_longer if verb == "pivot_longer" else
            self._order_by if verb == "order_by" else
            self._subset if verb in ("limit", "head", "sample") else
            self._distinct if verb == "distinct" else
            self._drop_null if verb == "drop_null" else
            self._fill_null if verb == "fill_null" else
            self._set_op if verb in ("union", "intersect", "difference") else
            self._unnest if verb == "unnest" else
            self._window if _window(step) else
            self._columns if verb in ("mutate", "select", "drop", "rename", "relocate", "cast", "try_cast") else
            self._flow
        )
        try:
            ins, out, rows = _run_on_samples(step, self.chain_rows)
            self.next_rows = {step.output.op(): (rows, out.partial)}  # for the next step, if it continues the chain
            handler(step, *self._with_facts(step, ins, out))
        except Exception as exc:  # ponytail: an op shape that a choreography does not handle gets the plain flow
            self.next_rows = {}
            print(f"ibis_anim: plain animation for step {step.op} ({exc!r})", file=sys.stderr)
            self._settle()
            self._flow(step, *_plain_samples(step))

    # -- choreographies ----------------------------------------------------------------------

    def _flow(self, step: Step, ins: list[Sample], out: Sample):
        """Fallback: each output column comes from the input column with the same name."""
        shown_out = _pick_columns(out.columns, step.created)
        shown_ins = [_pick_columns(s.columns, [*step.used, *shown_out]) for s in ins]
        width = _cell_width([*ins, out], [c for cs in [shown_out, *shown_ins] for c in cs])
        grids = [Grid(s, shown, width, used=step.used) for s, shown in zip(ins, shown_ins)]
        res = Grid(out, shown_out, width, created=step.created)
        self._side_by_side([*grids, res])
        self._enter(*grids)
        for g in grids:
            self._highlight(g, step.used)
        anims = []
        for name in shown_out:
            src = next((g.column(name) for g in grids if name in g.shown and name not in step.created), None)
            anims.append(TransformFromCopy(src, res.column(name)) if src else FadeIn(res.column(name), shift=RIGHT * 0.3))
        self.play(FadeIn(res.title), LaggedStart(*anims, lag_ratio=0.15), run_time=2.5)
        self._settle(*grids, res)
        self.result = res

    def _subset(self, step: Step, ins: list[Sample], out: Sample, test: str | None = None):
        """Each row gets a mark when there is a test; removed rows turn red and leave; the others slide up."""
        s = ins[0]
        idx = [i for i in _match(out.rows, s.rows, [(c, c) for c in s.columns]) if i is not None]
        keep = [i in idx for i in range(len(s.rows))]
        grid = Grid(s, _pick_columns(s.columns, step.used), _cell_width([s], s.columns), used=step.used)
        self._in_place(grid)
        self._enter(grid)
        self._highlight(grid, step.used)
        verb = step.op.split()[-1]
        if test is not None:
            self._say(f"test each row: {test}")
        elif verb in ("limit", "head") and len(idx) < len(s.rows):
            n = len(idx)
            y = (grid.slot(n - 1, 0)[1] + grid.slot(n, 0)[1]) / 2
            cut = DashedLine([grid.get_left()[0] - 0.2, y, 0], [grid.get_right()[0] + 0.2, y, 0], color=RED)
            self._say(f"keep the first {n} rows")
            self.play(FadeIn(cut), FadeIn(Text(f"limit {n}", font=SANS, font_size=18, color=RED).next_to(cut, RIGHT, buff=0.1)))
        else:
            self._say(f"keep {len(idx)} of the {len(s.rows)} rows")
        self._subset_marks(grid, keep, marks=test is not None)
        self._retitle(grid, out)
        self._refresh_facts(grid, out)
        self.result = grid

    def _filter(self, step: Step, ins: list[Sample], out: Sample):
        self._subset(step, ins, out, test=_call_args(step.code, "filter"))

    def _distinct(self, step: Step, ins: list[Sample], out: Sample):
        self._subset(step, ins, out, test="is this the first time that the row appears?")

    def _drop_null(self, step: Step, ins: list[Sample], out: Sample):
        op = step.output.op()
        cols = ", ".join(f.name for f in op.subset) if op.subset else "any column"
        self._subset(step, ins, out, test=f"{'all values' if op.how == 'all' else 'a value'} in {cols} is null → remove")

    def _fill_null(self, step: Step, ins: list[Sample], out: Sample):
        """Null cells light up red, then take the replacement value."""
        s, repl = ins[0], step.output.op().replacements
        repl = dict(repl) if hasattr(repl, "items") else {c: repl for c in s.columns}
        filled = [c for c in s.columns if c in repl]
        out, idx = _in_input_order(out, _match(out.rows, s.rows, [(c, c) for c in s.columns if c not in filled]))
        shown = _pick_columns(s.columns, filled)
        width = _cell_width([s, out], shown)
        inp = Grid(s, shown, width, used=filled)
        res = Grid(out, shown, width, created=filled)
        self._in_place(inp, res)
        self._enter(inp)
        self._say("fill nulls: " + ", ".join(f"{c} → {_expr_text(repl[c])}" for c in filled if c in shown))
        self._highlight(inp, filled)
        nulls = [(r, c) for r, row in enumerate(s.rows) for c in filled if c in shown and row[c] is None]
        if not nulls:
            self._say("there is no null in the rows shown")
            self.result = inp
            return
        self.play(*[Circumscribe(inp.cell(r, c), color=RED, buff=0.02) for r, c in nulls], run_time=1.2)
        self._say("each null takes the replacement value", wait=0.2)
        self.play(LaggedStart(*[Transform(inp.cell(r, c), res.cell(k, c)) for k, r in enumerate(idx) for c in filled
                                if r is not None and (r, c) in nulls], lag_ratio=0.3), run_time=2)
        self._settle(res)
        self.result = res

    def _set_op(self, step: Step, ins: list[Sample], out: Sample):
        """Union appends the rows of the second table; intersect and difference test each row."""
        a, b = ins
        verb, distinct = step.op.split()[-1], step.output.op().distinct
        shown = _pick_columns(a.columns, [])
        width = _cell_width([a, b], shown)
        in_b = [any(all(ra[c] == rb[c] for c in a.columns) for rb in b.rows) for ra in a.rows]
        colors, shared = {}, [r for r, hit in zip(a.rows, in_b) if hit]
        ga, gb = Grid(a, shown, width), Grid(b, shown, width)
        for grid in (ga, gb):  # rows that are in both tables get the same colour
            for r, row in enumerate(grid.sample.rows):
                if row in shared:
                    for n in shown:
                        grid.cell(r, n)[0].set_fill(_color(colors, tuple(row[c] for c in a.columns)), 0.45)
        if verb == "union":
            extra = Grid(replace(a, rows=a.rows + b.rows), shown, width)  # only to size the layout
            self._side_by_side([extra, gb], gap=1.2)
            ga.scale(extra.cell_w / ga.cell_w, about_point=ORIGIN).shift(extra.slot(-1, 0) - ga.slot(-1, 0))
            self._enter(ga, gb)
            self._say(f"union: the rows of {step.labels[1]} go under the rows of {step.labels[0]}")
            n = len(a.rows)
            self.play(*[gb.row(j).animate(path_arc=0.6).move_to(ga.slot(n + j, (len(shown) - 1) / 2)) for j in range(len(b.rows))],
                      FadeOut(gb.title), *[FadeOut(cell) for (r, _c), cell in gb.cells.items() if r < 0], run_time=2.5)  # header, types, facts
            rows = [ga.row(i) for i in range(n)] + [gb.row(j) for j in range(len(b.rows))]
            combined = a.rows + b.rows
            keep = [i in set(_match(out.rows, combined, [(c, c) for c in a.columns])) for i in range(len(combined))]
            if distinct and not all(keep):
                self._say("distinct: rows that repeat an earlier row leave")
                gone = [i for i, k in enumerate(keep) if not k]
                self.play(*[c[0].animate.set_fill(RED_E, 0.7) for i in gone for c in rows[i]], run_time=0.8)
                self._drop(gb, [rows[i] for i in gone])
                kept = [i for i, k in enumerate(keep) if k]
                self.play(*[rows[i].animate.shift(UP * (ga.slot(j, 0)[1] - ga.slot(i, 0)[1])) for j, i in enumerate(kept) if j != i])
            self._refresh_facts(ga, out)
            return
        self._side_by_side([ga, gb], gap=1.5)
        self._enter(ga, gb)
        word = "is also in" if verb == "intersect" else "is not in"
        self._say(f"{verb}: keep each row of {step.labels[0]} that {word} {step.labels[1]} (same colour = same row)")
        self._subset_marks(ga, [hit if verb == "intersect" else not hit for hit in in_b])
        self.play(FadeOut(gb))
        self._refresh_facts(ga, out)

    def _retitle(self, grid: Grid, out: Sample):
        """An in-place step keeps drawing its input table: at the end, its title names the result."""
        title = Text(f"result · {out.total} rows" + (" in full" if out.partial else ""), font=SANS, font_size=18, color=GREY_A)
        self.play(Transform(grid.title, title.scale(grid.title.height / title.height).move_to(grid.title, aligned_edge=LEFT)))

    def _refresh_facts(self, grid: Grid, out: Sample):
        """An in-place step keeps drawing its input table: at the end, its facts row takes the facts of the result."""
        anims = []
        for name in grid.shown:
            old = grid.cells.get((-3, name))
            if old is not None:
                unscaled_width = old[0].width * FACTS_H / old[0].height  # the grid can be scaled: keep the cell's proportions
                new = _facts_cell(out.facts.get(name, ""), unscaled_width, name in out.changed)
                anims.append(Transform(old, new.match_width(old[0]).move_to(old[0])))
        if anims:
            self.play(*anims, run_time=0.8)

    def _subset_marks(self, grid: Grid, keep: list[bool], marks=True):
        """Optional ✔/✘ marks; the rows to remove turn red and leave; the other rows slide up."""
        signs = [Text("✔" if k else "✘", font=SANS, font_size=24, color=GREEN if k else RED) for k in keep] if marks else []
        for r, sign in enumerate(signs):
            sign.scale_to_fit_height((grid.slot(0, 0)[1] - grid.slot(1, 0)[1]) * 0.6)
            sign.move_to(grid.slot(r, len(grid.shown)) + LEFT * grid.cell_w * 0.3)
        if signs:
            self.play(LaggedStart(*[FadeIn(m, scale=1.6) for m in signs], lag_ratio=0.35), run_time=0.5 * len(signs))
        gone = [r for r, k in enumerate(keep) if not k]
        if not gone:
            return
        self.play(*[c[0].animate.set_fill(RED_E, 0.7) for r in gone for c in grid.row(r)], run_time=0.8)
        self._drop(grid, [grid.row(r) for r in gone] + [signs[r] for r in gone if signs])
        kept = [r for r, k in enumerate(keep) if k]
        moves = [m.animate.shift(UP * (grid.slot(j, 0)[1] - grid.slot(r, 0)[1]))
                 for j, r in enumerate(kept) if j != r for m in [grid.row(r), *([signs[r]] if signs else [])]]
        if moves:
            self.play(*moves, run_time=1.2)

    def _unnest(self, step: Step, ins: list[Sample], out: Sample):
        """Each array cell splits into its elements, and each element gets its own row."""
        s, op = ins[0], step.output.op()
        col, new = op.column.name, op.column_name
        others = [c for c in s.columns if c != col and c in out.columns]
        src_of = _match(out.rows, s.rows, [(c, c) for c in others], reuse=True)
        order = sorted(range(len(out.rows)), key=lambda k: (src_of[k] is None, src_of[k] or 0))[:MAX_RESULT_ROWS]
        out, src_of = replace(out, rows=[out.rows[k] for k in order]), [src_of[k] for k in order]
        in_shown = _pick_columns(s.columns, [col])
        out_shown = [c for c in out.columns if c in in_shown or c == new]
        width = _cell_width([s, out], [*in_shown, *out_shown])
        inp, res = Grid(s, in_shown, width, used=[col]), Grid(out, out_shown, width, created=[new])
        self._side_by_side([inp, res], gap=1.5)
        self._enter(inp)
        self._say(f"unnest {col}: each element of the array gets its own row; the other cells are copied")
        self._highlight(inp, [col])
        self.play(FadeIn(res.title), *[TransformFromCopy(inp.header(c), res.header(c)) for c in out_shown if c in inp.shown],
                  *[FadeIn(res.header(c)) for c in out_shown if c not in inp.shown])
        for i in range(len(s.rows)):
            targets = [k for k, src in enumerate(src_of) if src == i]
            if not targets:
                self.play(Indicate(inp.cell(i, col), color=RED), run_time=0.6)  # an empty array gives no row
                continue
            pieces = VGroup(*[res.cell(k, new).copy() for k in targets]).arrange(RIGHT, buff=0.02)
            pieces.scale_to_fit_width(inp.cell(i, col).width * 0.95).move_to(inp.cell(i, col))
            self.play(TransformFromCopy(inp.cell(i, col), pieces), run_time=0.8)
            anims = [Transform(p, res.cell(k, new).copy()) for p, k in zip(pieces, targets)]
            copies = [(inp.cell(i, c).copy(), res.cell(k, c)) for k in targets for c in out_shown if c != new and c in inp.shown]
            anims += [Transform(cp, dst.copy()) for cp, dst in copies]
            self.play(*anims, run_time=1.3)
            self.remove(pieces, *[cp for cp, _ in copies])
            self.add(*[res.cell(k, c) for k in targets for c in out_shown])
        self._settle(inp, res)
        self.result = res

    def _order_by(self, step: Step, ins: list[Sample], out: Sample):
        """Highlight the sort keys, draw their direction, and move each row to its new position."""
        s = ins[0]
        keys = [(k.arg.name, k.ascending) for k in step.output.op().keys if isinstance(k.arg, ops.Field)]
        names = [k for k, _ in keys]
        grid = Grid(s, _pick_columns(s.columns, names), _cell_width([s], s.columns), used=names)
        self._in_place(grid)
        self._enter(grid)
        words = ", ".join(f"{n} ({'ascending' if a else 'descending'})" for n, a in keys)
        self._say(f"sort by {words}")
        self._highlight(grid, names)
        arrow = self._sort_arrow(grid, keys)
        self.play(FadeIn(arrow, shift=DOWN * 0.3))
        perm = [i for i in _match(out.rows, s.rows, [(c, c) for c in s.columns]) if i is not None]
        self._say("each row moves to its sorted position", wait=0.3)
        if not self._reorder(grid, perm):
            self._say("the rows are already in this order", wait=0.3)
        self.play(LaggedStart(*[Indicate(grid.cell(r, names[0]), color=YELLOW) for r in range(len(perm))], lag_ratio=0.25), run_time=2)
        self._retitle(grid, out)
        self._refresh_facts(grid, out)
        self.result = grid

    def _columns(self, step: Step, ins: list[Sample], out: Sample):
        """Carried columns keep or change place; per row, the source cells merge into each new cell."""
        s, verb = ins[0], step.op.split()[-1]
        renamed = {old: new for new, old in step.kwargs.items() if isinstance(old, str)} if verb == "rename" else {}
        sources = _sources(step)
        computed = [c for c in out.columns if c in step.created and c not in renamed.values()]
        carried = {c: c for c in out.columns if c not in step.created and c in s.columns}
        carried |= {new: old for old, new in renamed.items() if new in out.columns}
        used = list(dict.fromkeys(f for c in computed for f in sources.get(c, []) if f in s.columns))
        new = [c for c in computed if c not in s.columns]
        keeps_all = all(c in carried.values() or c in computed for c in s.columns)
        # a mutate keeps every input column, so leave room for the new ones
        kept_inputs = [carried[o] for o in out.columns if o in carried]  # a select shows what it keeps
        dropped_all = [c for c in s.columns if c not in carried.values() and c not in computed][:2]  # and some of what it drops
        kept_or_dropped = [*dropped_all, *kept_inputs] if verb == "drop" else [*kept_inputs, *dropped_all]
        in_shown = _pick_columns(s.columns, [*used, *renamed, *(c for c in computed if c in s.columns), *kept_or_dropped],
                                 MAX_COLUMNS - len(new) if keeps_all else MAX_COLUMNS)
        out_shown = [o for o in out.columns if carried.get(o) in in_shown or o in new or o in computed and o in in_shown]
        out, idx = _in_input_order(out, _match(out.rows, s.rows, list(carried.items())))
        width = _cell_width([s, out], [*in_shown, *out_shown])
        retyped = [c for c in computed if c in s.types and out.types.get(c) != s.types[c]]
        inp = Grid(s, in_shown, width, used=[*used, *renamed])
        res = Grid(out, out_shown, width, created=[*computed, *renamed.values()])
        inplace = out_shown[: len(in_shown)] == in_shown  # the table only grows to the right
        reshape = not inplace and not new  # columns leave, move or change name, in place
        self._in_place(inp, res) if inplace or reshape else self._side_by_side([inp, res], gap=1.5)
        self._enter(inp)
        if inplace and not any(c in computed for c in out_shown):
            self._say("no change in the columns shown")
        if reshape:
            dropped = [c for c in in_shown if c not in carried.values() and c not in computed]
            if dropped:
                self._say(f"drop {', '.join(dropped)}")
                self.play(*[c[0].animate.set_fill(RED_E, 0.6) for n in dropped for c in inp.column(n)], run_time=0.8)
                self._drop(inp, [inp.column(n) for n in dropped], shift=DOWN * 0.4)
            kept = [(o, carried[o]) for o in out_shown if carried.get(o) in in_shown]
            shifts = [inp.column(i).animate.shift(RIGHT * (res.slot(-1, res.shown.index(o))[0] - inp.slot(-1, inp.shown.index(i))[0]))
                      for o, i in kept if res.shown.index(o) != inp.shown.index(i)]
            if shifts:
                self._say("the other columns close the gaps" if dropped else "move the columns to their new place")
                self.play(*shifts, run_time=1.4)
            renames = [(o, i) for o, i in kept if o != i]
            if renames:
                self._say(f"rename {', '.join(f'{i} → {o}' for o, i in renames)}")
                self.play(*[Transform(inp.header(i), res.header(o)) for o, i in renames], run_time=1.2)
        elif not inplace:
            dropped = [c for c in in_shown if c not in carried.values()]
            if dropped:
                self._say(f"drop {', '.join(dropped)}")
                self.play(*[c[0].animate.set_fill(RED_E, 0.6) for n in dropped for c in inp.column(n)], run_time=0.8)
                self.play(*[inp.column(n).animate.set_opacity(0.3) for n in dropped])
            kept = [(o, carried[o]) for o in out_shown if carried.get(o) in in_shown]
            if kept:
                moves = [f"{i} → {o}" for o, i in kept if o != i]
                self._say(f"rename {', '.join(moves)}" if moves else f"keep {', '.join(o for o, _ in kept)}")
                self.play(FadeIn(res.title), LaggedStart(*[TransformFromCopy(inp.column(i), res.column(o)) for o, i in kept], lag_ratio=0.2), run_time=2)
        for name in [c for c in out_shown if c in computed]:
            reads = [f for f in sources.get(name, []) if f in in_shown]
            self._say(_formula(step, name), wait=0.3)
            self._highlight(inp, reads)
            boxes = [res.cell(r, name)[0] for r in range(len(out.rows))]
            self.play(FadeIn(res.header(name)), *[FadeIn(b) for b in boxes], run_time=0.6)
            groups = [[(inp.cell(i, f), res.cell(k, name)) for f in reads] for k, i in enumerate(idx) if i is not None and reads]
            if groups:
                self._say("the same thing happens on every row", wait=0)
                self._fly(groups, run_time=1.4 + 0.35 * len(groups), lag_ratio=0.3)
            else:
                self.play(*[FadeIn(res.cell(k, name)) for k in range(len(out.rows))])
            if (inplace or reshape) and name in in_shown:
                self.remove(*inp.column(name, header=False))
            if name in retyped and name in in_shown:
                self._say(f"the type of {name} changes: {s.types[name]} → {out.types[name]}", wait=0.2)
                self.play(Transform(inp.type_cell(name), res.type_cell(name)), run_time=1)
                self.play(Circumscribe(res.type_cell(name), color=GREEN, buff=0.03), run_time=1)
        self._settle(*([res] if inplace or reshape else [inp, res]))
        self.result = res

    def _window(self, step: Step, ins: list[Sample], out: Sample):
        """Colour the partitions, sort the rows, then slide the window frame down the rows."""
        s = ins[0]
        target, w = _window(step)
        partition = [g.name for g in w.group_by if isinstance(g, ops.Field)]
        order = [(k.arg.name, k.ascending) for k in w.order_by if isinstance(k.arg, ops.Field)]
        value = next((f.name for f in w.func.find(ops.Field, filter=ops.Value)), None)
        important = [*partition, *(n for n, _ in order), *([value] if value else [])]
        in_shown = _pick_columns(s.columns, important, MAX_COLUMNS - 1)
        out_shown = [c for c in out.columns if c in in_shown or c == target]
        carried = [(c, c) for c in s.columns if c in out.columns and c not in step.created]
        out, idx = _in_input_order(out, _match(out.rows, s.rows, carried))
        width = _cell_width([s, out], out_shown)
        colors = {}
        inp = Grid(s, in_shown, width, used=important)
        self._in_place(inp, Grid(s, [*in_shown, target], width))  # the second grid only sizes the layout
        self._enter(inp)
        if partition:
            self._say(f"partition by {', '.join(partition)}: one colour per group")
            self._highlight(inp, partition)
            self.play(*self._tint(inp, partition, colors), run_time=1)
        group_rank = _first_seen(s.rows, partition)
        perm = _sort_perm(s.rows, order)
        perm.sort(key=lambda i: group_rank[_hashable(tuple(s.rows[i][k] for k in partition))])
        if order:
            self._say(f"order by {', '.join(n for n, _ in order)} inside each group")
            arrow = self._sort_arrow(inp, order)
            self.play(FadeIn(arrow, shift=DOWN * 0.3))
            if not self._reorder(inp, perm):
                self.play(LaggedStart(*[Indicate(inp.cell(r, order[0][0])) for r in range(len(perm))], lag_ratio=0.2))
            self.play(FadeOut(arrow))
        # the output rows, in the order of the sorted input rows
        position = {i: k for k, i in enumerate(idx)}
        out = replace(out, rows=[out.rows[position[i]] for i in perm if i in position])
        res = Grid(out, out_shown, width, created=[target], keys=partition, colors=colors)
        res.scale(inp.cell_w / res.cell_w, about_point=ORIGIN).shift(inp.slot(-1, 0) - res.slot(-1, 0))
        func = type(w.func).__name__.lower()
        ranking = isinstance(w.func, (ops.RankBase, ops.RowNumber)) and order
        if ranking:  # a rank compares the row with its whole group, so the frame is the group
            by = ", ".join(f"{n} ({'ascending' if a else 'descending'})" for n, a in order)
            self._say(f"{_formula(step, target)}  ·  the position of the row inside its group, by {by}")
        else:
            self._say(f"{_formula(step, target)}  ·  the window of {func}({value or ''}) goes {_frame_text(w)}")
        boxes = [res.cell(r, target)[0] for r in range(len(out.rows))]
        self.play(FadeIn(res.header(target)), *[FadeIn(b) for b in boxes], run_time=0.6)
        keys = [tuple(r[k] for k in partition) for r in inp.sample.rows]
        frame = None
        for k in range(min(len(inp.sample.rows), len(out.rows))):
            part = [i for i, key in enumerate(keys) if key == keys[k]]
            lo = part[0] if w.start is None else max(part[0], k + _offset(w.start))
            hi = part[-1] if w.end is None else min(part[-1], k + _offset(w.end))  # ibis: no bound = unbounded
            cells = [inp.cell(r, value) for r in range(lo, hi + 1)] if value else []
            movers = cells
            if ranking:
                cells, movers = [inp.cell(r, order[0][0]) for r in part], [inp.cell(k, order[0][0])]
            box = VGroup(SurroundingRectangle(VGroup(*cells) if cells else inp.row(k), color=YELLOW, buff=0.03),
                         SurroundingRectangle(res.cell(k, target), color=YELLOW, buff=0.03))
            self.play(FadeIn(box) if frame is None else Transform(frame, box), run_time=0.5)
            frame = frame or box
            self._fly([[(c, res.cell(k, target)) for c in movers]] if movers else [[(inp.row(k), res.cell(k, target))]], run_time=0.8)
        self.play(FadeOut(frame))
        self._settle(res)
        self.result = res

    def _aggregate(self, step: Step, ins: list[Sample], out: Sample):
        """Colour the rows by group, gather each group, then collapse each group into one row."""
        s, keys = ins[0], step.group_by
        sources = _sources(step)
        metrics = [c for c in out.columns if c not in keys]
        reads = list(dict.fromkeys(f for m in metrics for f in sources.get(m, []) if f in s.columns))
        in_shown = _pick_columns(s.columns, [*keys, *reads])
        rank = _first_seen(s.rows, keys)
        out = replace(out, rows=sorted(out.rows, key=lambda r: rank.get(_hashable(tuple(r[k] for k in keys)), len(rank)))[:MAX_RESULT_ROWS])
        out_shown = _pick_columns(out.columns, [*keys, *metrics])
        width = _cell_width([s, out], [*in_shown, *out_shown])
        colors = {}
        inp = Grid(s, in_shown, width, used=[*keys, *reads])
        res = Grid(out, out_shown, width, used=keys, created=metrics, keys=keys, colors=colors)
        self._side_by_side([inp, res], gap=1.6)
        self._enter(inp)
        if keys:
            self._say(f"group by {', '.join(keys)}: rows with the same {', '.join(keys)} get the same colour")
            self._highlight(inp, keys)
            self.play(*self._tint(inp, keys, colors), run_time=1.2)
            self._say("gather the rows of each group")
            self._reorder(inp, sorted(range(len(s.rows)), key=lambda i: rank[_hashable(tuple(s.rows[i][k] for k in keys))]))
        formulas = "; ".join(_formula(step, m) for m in metrics)
        self._say(f"each group becomes one row: {formulas}" if keys else f"all the rows become one row: {formulas}")
        self.play(FadeIn(res.title),
                  *[TransformFromCopy(inp.header(k), res.header(k)) for k in keys if k in res.shown and k in inp.shown],
                  *[FadeIn(res.header(m)) for m in metrics if m in res.shown],
                  *[FadeIn(res.cell(r, n)[0]) for r in range(len(out.rows)) for n in res.shown], run_time=1)
        in_keys = [tuple(r[k] for k in keys) for r in inp.sample.rows]
        for g, row in enumerate(out.rows):
            members = [r for r, key in enumerate(in_keys) if key == tuple(row[k] for k in keys)]
            if not members:
                self.play(*[FadeIn(res.cell(g, n)) for n in res.shown])
                continue
            outline = SurroundingRectangle(VGroup(*[inp.row(r) for r in members]), color=YELLOW, buff=0.04)
            self.play(FadeIn(outline), run_time=0.4)
            pairs = [(inp.cell(r, k), res.cell(g, k)) for r in members for k in keys if k in inp.shown and k in res.shown]
            for m in [m for m in metrics if m in res.shown]:
                fields = [f for f in sources.get(m, []) if f in inp.shown]
                pairs += [(inp.cell(r, f), res.cell(g, m)) for r in members for f in fields] or [(inp.row(r), res.cell(g, m)) for r in members]
            self._fly([pairs], run_time=1.8)
            self.play(FadeOut(outline), run_time=0.3)
            self.wait(0.4)
        self._settle(inp, res)
        self.result = res

    def _join(self, step: Step, ins: list[Sample], out: Sample):
        """Move the keys to the inner edges, bring the tables together, line up the rows, merge the keys."""
        L, R = ins
        how = step.kwargs.get("how") or (step.args[2] if len(step.args) > 2 else "inner")
        lk = [c for c in step.used if c in L.columns]
        rk = [c for c in step.used if c in R.columns]
        if not lk or len(lk) != len(rk):
            raise ValueError("join keys not found")
        if how in ("semi", "anti"):
            keys = " and ".join(f"{a} = {step.labels[1]}.{b}" for a, b in zip(lk, rk))
            return self._subset(step, [L], out, test=f"{'is there' if how == 'semi' else 'is there no'} row of {step.labels[1]} where {keys}?")
        lmap = {c: c for c in L.columns if c in out.columns}
        rmap = {c: c if c in out.columns and c not in L.columns else f"{c}_right" if f"{c}_right" in out.columns else None
                for c in R.columns}
        lidx = _match(out.rows, L.rows, list(lmap.items()), reuse=True)
        rpairs = [(o, c) for c, o in rmap.items() if o] + [(lmap[a], b) for a, b in zip(lk, rk) if not rmap.get(b)]
        ridx = _match(out.rows, R.rows, rpairs, reuse=True)
        order = sorted(range(len(out.rows)), key=lambda k: (lidx[k] is None, lidx[k] or 0, ridx[k] is None, ridx[k] or 0))[:MAX_RESULT_ROWS]
        out = replace(out, rows=[out.rows[k] for k in order])
        lidx, ridx = [lidx[k] for k in order], [ridx[k] for k in order]
        lnon = [c for c in L.columns if c not in lk][:3]
        rnon = [c for c in R.columns if c not in rk and rmap.get(c)][:3]
        merged = [*(lmap[c] for c in lnon), *(lmap[c] for c in lk), *(rmap[c] for c in rnon)]
        width = _cell_width([L, R, out], [*L.columns, *R.columns, *merged])
        colors = {}
        lg = Grid(L, [c for c in L.columns if c in lnon or c in lk], width, used=lk, keys=lk, colors=colors)
        rg = Grid(R, [c for c in R.columns if c in rnon or c in rk], width, used=rk, keys=rk, colors=colors)
        mg = Grid(out, merged, width, used=[lmap[c] for c in lk], keys=[lmap[c] for c in lk], colors=colors)
        nk = len(lk)
        span = width * (len(merged) + nk)
        f = self._scale([lg, rg, mg], max(lg.width + rg.width + 1.2, span), max(lg.height, rg.height, mg.height))
        self._put(lg, STAGE_X - (lg.width + rg.width + 1.2 * f) / 2)
        self._put(rg, lg.get_right()[0] + 1.2 * f)
        self._put(mg, STAGE_X - span * f / 2)
        self._enter(lg, rg)
        self._say(f"{how} join where " + " and ".join(f"{step.labels[0]}.{a} = {step.labels[1]}.{b}" for a, b in zip(lk, rk)))
        self.play(*[Circumscribe(lg.column(c), color=YELLOW, buff=0.03) for c in lk],
                  *[Circumscribe(rg.column(c), color=YELLOW, buff=0.03) for c in rk], run_time=1.2)
        # 1. the join columns move to the inner edges of the two tables
        self._say(f"move the join columns: right edge of {step.labels[0]}, left edge of {step.labels[1]}")
        new_l, new_r = [*lnon, *lk], [*rk, *rnon]
        shifts = [lg.column(c).animate(path_arc=-0.6).shift(RIGHT * (lg.slot(-1, new_l.index(c))[0] - lg.slot(-1, lg.shown.index(c))[0]))
                  for c in lg.shown if new_l.index(c) != lg.shown.index(c)]
        shifts += [rg.column(c).animate(path_arc=-0.6).shift(RIGHT * (rg.slot(-1, new_r.index(c))[0] - rg.slot(-1, rg.shown.index(c))[0]))
                   for c in rg.shown if new_r.index(c) != rg.shown.index(c)]
        if shifts:
            self.play(*shifts, run_time=1.5)
        lg.shown, rg.shown = new_l, new_r
        # 2. the tables move together, so that the join columns touch
        self._say("bring the tables together on the join columns")
        self.play(lg.animate.shift(mg.slot(-1, 0) - lg.slot(-1, 0)),
                  rg.animate.shift(mg.slot(-1, len(new_l)) - rg.slot(-1, 0)), run_time=1.5)
        # 3. the rows line up so that equal keys sit side by side
        self._say("line up the rows with equal keys; rows without a match leave" if how == "inner" else "line up the rows with equal keys")
        anims, right_copies = [], []
        for grid, idx in ((lg, lidx), (rg, ridx)):
            moved = set()
            for k, i in enumerate(idx):
                if i is None:
                    continue
                row = grid.row(i) if i not in moved else grid.row(i).copy()  # a row with two matches is copied
                if i in moved and grid is rg:
                    right_copies.append(row)
                moved.add(i)
                anims.append(row.animate.shift(UP * (mg.slot(k, 0)[1] - grid.slot(i, 0)[1])))
            unmatched = [grid.row(i) for i in range(len(grid.sample.rows)) if i not in moved]
            if unmatched:
                self.play(*[c[0].animate.set_fill(RED_E, 0.7) for row in unmatched for c in row], run_time=0.6)
                self._drop(grid, unmatched, shift=DOWN * 0.3)
        self.play(*anims, run_time=2.5)
        self.wait(0.5)
        # 4. the join columns overlap and merge
        self._say("the join columns overlap: equal keys become one column")
        self.play(VGroup(rg, *right_copies).animate.shift(LEFT * nk * mg.cell_w), run_time=1.5)
        self._settle(mg)
        self.result = mg
        self.play(*[Indicate(mg.column(lmap[c], header=False), color=YELLOW) for c in lk], run_time=1.2)

    def _pivot_wider(self, step: Step, ins: list[Sample], out: Sample):
        """Distinct names become columns, the other columns become rows, values move to their cell."""
        s, src = ins[0], step.inputs[0]
        names = [c for c in _names(step.kwargs.get("names_from", "name"), src) if c in s.columns]
        values = [c for c in _names(step.kwargs.get("values_from", "value"), src) if c in s.columns]
        if len(names) != 1 or len(values) != 1:
            raise ValueError("pivot_wider with one names_from and one values_from column only")
        name_col, value_col = names[0], values[0]
        prefix = step.kwargs.get("names_prefix", "")
        ids = [c for c in out.columns if c in s.columns and c not in (name_col, value_col)]
        pivoted = [c for c in out.columns if c not in ids]
        rank = _first_seen(s.rows, ids)
        out = replace(out, rows=sorted(out.rows, key=lambda r: rank.get(_hashable(tuple(r[k] for k in ids)), len(rank))))
        out_row = {key: k for k, key in enumerate(_hashable(tuple(r[c] for c in ids)) for r in out.rows)}
        column_of = {r[name_col]: f"{prefix}{r[name_col]}" for r in s.rows}
        in_shown = _pick_columns(s.columns, [name_col, value_col, *ids])
        out_shown = _pick_columns(out.columns, [*ids, *pivoted], MAX_COLUMNS + 2)
        width = _cell_width([s, out], [*in_shown, *out_shown])
        inp = Grid(s, in_shown, width, used=[name_col])
        res = Grid(out, out_shown, width, created=pivoted)
        self._side_by_side([inp, res], gap=1.6)
        self._enter(inp)
        # 1. distinct names become column headers
        self._say(f"names_from = {name_col}: each distinct value becomes a column")
        self._highlight(inp, [name_col])
        heads = [[(inp.cell(r, name_col), res.header(column_of[row[name_col]]))] for r, row in enumerate(s.rows)
                 if column_of[row[name_col]] in res.shown]
        self._fly(heads, run_time=2.5, lag_ratio=0.2, path_arc=-0.8)
        # 2. the other columns become one row per distinct value
        self._say(f"the other columns ({', '.join(ids)}): one row for each distinct value")
        self._highlight(inp, ids, color=BLUE_D)
        pairs = [[(inp.header(c), res.header(c)) for c in ids if c in inp.shown and c in res.shown]]
        pairs += [[(inp.cell(r, c), res.cell(out_row[_hashable(tuple(row[i] for i in ids))], c)) for c in ids if c in inp.shown and c in res.shown]
                  for r, row in enumerate(s.rows) if _hashable(tuple(row[i] for i in ids)) in out_row]
        self._fly(pairs, run_time=2.5, lag_ratio=0.15)
        self.play(FadeIn(res.title))
        # 3. the new cells start empty
        self._say("the new cells start empty")
        boxes = [res.cell(r, c)[0] for r in range(len(out.rows)) for c in pivoted if c in res.shown]
        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.05), run_time=1)
        self.play(*[b.animate.set_stroke(YELLOW, 3) for b in boxes], run_time=0.5)
        self.play(*[b.animate.set_stroke(GREY_B, 1) for b in boxes], run_time=0.5)
        # 4. each value moves to the cell of its row and column
        self._say(f"values_from = {value_col}: each value moves to the cell of its row and column")
        self._highlight(inp, [value_col], color=GREEN)
        moves = [[(inp.cell(r, value_col), res.cell(out_row[_hashable(tuple(row[i] for i in ids))], column_of[row[name_col]]))]
                 for r, row in enumerate(s.rows)
                 if _hashable(tuple(row[i] for i in ids)) in out_row and column_of[row[name_col]] in res.shown]
        self._fly(moves, run_time=3, lag_ratio=0.25, path_arc=0.6)
        self._settle(inp, res)
        self.result = res

    def _pivot_longer(self, step: Step, ins: list[Sample], out: Sample):
        """For each input row, the cells of the pivoted columns rotate into a column of the result."""
        s = ins[0]
        names_to, values_to = step.kwargs.get("names_to", "name"), step.kwargs.get("values_to", "value")
        if not isinstance(names_to, str) or not isinstance(values_to, str):
            raise ValueError("pivot_longer with one names_to column only")
        pivoted = [c for c in s.columns if c in step.used]
        ids = [c for c in s.columns if c not in pivoted]
        src_of = _match(out.rows, s.rows, [(c, c) for c in ids], reuse=True)
        order = sorted(range(len(out.rows)), key=lambda k: (src_of[k] is None, src_of[k] or 0,
                       pivoted.index(out.rows[k][names_to]) if out.rows[k][names_to] in pivoted else 0))[:MAX_RESULT_ROWS]
        out = replace(out, rows=[out.rows[k] for k in order])
        src_of = [src_of[k] for k in order]
        in_shown = _pick_columns(s.columns, pivoted)
        id_shown = [c for c in in_shown if c in ids]
        out_shown = [*id_shown, names_to, values_to]
        width = _cell_width([s, out], [*in_shown, *out_shown])
        inp = Grid(s, in_shown, width, used=pivoted)
        res = Grid(out, out_shown, width, created=[names_to, values_to])
        self._side_by_side([inp, res], gap=1.6)
        self._enter(inp)
        self._say(f"the columns {', '.join(pivoted)} become rows: {names_to} gets the column name, {values_to} the value")
        self._highlight(inp, pivoted)
        self.play(FadeIn(res.title), *[TransformFromCopy(inp.header(c), res.header(c)) for c in id_shown],
                  FadeIn(res.header(names_to)), FadeIn(res.header(values_to)), run_time=1)
        for i in range(len(s.rows)):
            targets = [k for k, src in enumerate(src_of) if src == i]
            if not targets:
                continue
            label = ", ".join(f"{c} = {_fmt(s.rows[i][c])}" for c in id_shown) or f"row {i + 1}"
            self._say(f"{label}: the {len(targets)} cells turn into a column")
            cols = [out.rows[k][names_to] for k in targets]
            heads = VGroup(*[inp.header(c).copy() for c in cols if c in inp.shown])
            cells = VGroup(*[inp.cell(i, c).copy() for c in cols if c in inp.shown])
            self.add(heads, cells)
            self.play(Rotate(heads, -PI / 2), Rotate(cells, -PI / 2), run_time=1.2)
            anims = [Transform(h, res.cell(k, names_to).copy()) for h, k in zip(heads, targets)]
            anims += [Transform(c, res.cell(k, values_to).copy()) for c, k in zip(cells, targets)]
            copies = [inp.cell(i, c).copy() for k in targets for c in id_shown]
            anims += [Transform(cp, res.cell(k, c).copy()) for cp, (k, c) in zip(copies, [(k, c) for k in targets for c in id_shown])]
            self.play(*anims, run_time=1.6)
            self.remove(heads, cells, *copies)
            self.add(*[res.cell(k, c) for k in targets for c in out_shown])
            self.wait(0.3)
        self._settle(inp, res)
        self.result = res
