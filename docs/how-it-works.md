# How ibis-anim works

`ibis_anim` records each ibis table call of a program while the program runs. After the run, it computes names, scope, facts and sample rows for the recorded calls. Then it gives the result to a manim scene, which renders one video section for each call.

This document explains the library as a set of small building blocks. Each block has one responsibility. Each section shows a small toy example that you can run, and the output of the real code.

- [1. The components](#1-the-components)
- [2. The drop-in replacement](#2-the-drop-in-replacement)
- [3. Block: the method wrapper](#3-block-the-method-wrapper)
- [4. Block: frame inspection](#4-block-frame-inspection)
- [5. Block: the `ast` module](#5-block-the-ast-module)
- [6. Block: table names](#6-block-table-names)
- [7. Block: chains, calls and scope](#7-block-chains-calls-and-scope)
- [8. Block: expression substitution](#8-block-expression-substitution)
- [9. Block: facts](#9-block-facts)
- [10. Block: sample rows for schema-only tables](#10-block-sample-rows-for-schema-only-tables)
- [11. Block: dbt-ibis models as input](#11-block-dbt-ibis-models-as-input)
- [12. Block: the re-run on the rows on the screen](#12-block-the-re-run-on-the-rows-on-the-screen)
- [13. Block: the scene](#13-block-the-scene)
- [14. One program from start to end](#14-one-program-from-start-to-end)
- [15. Known limits](#15-known-limits)

To run the examples, use the virtual environment of the project, and turn off the video at exit:

```sh
export IBIS_ANIM=0 PYTHONIOENCODING=utf-8
.venv/Scripts/python example.py
```

The examples of sections 3, 4 and 5 use only the standard library. The other examples import `ibis_anim`, `ibis`, `portion` or `manim`. Each output in this document comes from a run of the code. Some outputs have extra spaces or line breaks for reading.

## 1. The components

The library has two phases:

1. **Record.** This phase runs inside the program. The method wrapper (section 3) records one `Step` for each outermost ibis table call. ibis expressions are lazy, so no data is read in this phase.
2. **Render.** This phase runs after the program, at exit. All other blocks run in this phase. They read the complete list of steps, so they know which step reads a table next, and which step reads it last.

The data goes through five stages:

```
 program runs                                  program exits
──────────────────────────────────────────┬────────────────────────────────────────────────────────────────
                                          │
  user code ──► wrapped Table methods ──► │ list[Step] ─┬─► table_names ──► chains / calls / plan ─┐
               (_tracer.py)               │             │   (_scope.py)                          │
                                          │             ├─► derive ──────► facts per table ──────┤
                                          │             │   (_derive.py, _facts.py)              │
                                          │             └─► for_step ────► rows for each step ───┤
                                          │                 (_samples.py)    on schema-only tables│
                                          │                                                      ▼
                                          │                            DebuggerScene (_scene.py)
                                          │                            for each step:
                                          │                              re-run on 6 rows ──► animation
                                          │                                                      │
                                          │                                                      ▼
                                          │                                            media/<name>.mp4
```

Each component has one responsibility:

| Component | Module | Reads | Gives | Responsibility |
| --- | --- | --- | --- | --- |
| Package front | [`__init__.py`](../ibis_anim/__init__.py) | the `ibis` module | the same names as `ibis` | Makes `import ibis_anim as ibis` work. Installs the wrapper. Registers the render at exit. |
| Command line | [`__main__.py`](../ibis_anim/__main__.py) | a file path | a video | Runs a script, a function or a dbt-ibis model under the tracer. |
| Tracer | [`_tracer.py`](../ibis_anim/_tracer.py) | ibis calls, Python frames, source files | `list[Step]` | Records each table call with its inputs, output, variables, source text and call stack. |
| Scope planner | [`_scope.py`](../ibis_anim/_scope.py) | `list[Step]` | names, chains, calls, scope frames | Pure functions. Decide what the debugger shows at each step. |
| Facts | [`_facts.py`](../ibis_anim/_facts.py), [`_derive.py`](../ibis_anim/_derive.py) | `list[Step]`, declarations | `TableFacts` per table | Abstract interpretation. Compute what is true for any data. |
| Sample generator | [`_samples.py`](../ibis_anim/_samples.py) | one `Step`, facts | rows for each schema-only table that the step reads | Make rows for one step on tables that have a schema and no data. |
| dbt loader | [`_dbt.py`](../ibis_anim/_dbt.py) | a dbt project | input tables, declarations | Run a dbt-ibis model and the models upstream of it. |
| Scene | [`_scene.py`](../ibis_anim/_scene.py) | all of the above | a video | Draw the debugger and animate each step. |

The planner, the facts and the sample generator do not import manim. You can test them without a render. The tests in `tests/` do this.

## 2. The drop-in replacement

A drop-in replacement must satisfy four conditions:

1. Each name of `ibis` is also a name of `ibis_anim`.
2. Each submodule of `ibis` is also a submodule of `ibis_anim`.
3. The objects are the same objects, so `isinstance` checks and type hints work.
4. The program gives the same results.

`ibis_anim` satisfies them with the four mechanisms below. None of them copies or changes ibis code.

### 2.1 The same names

[`__init__.py`](../ibis_anim/__init__.py) does a star import of ibis. Then it defines a module-level `__getattr__` and `__dir__` (PEP 562):

```python
import ibis
from ibis import *  # noqa: F403

def __getattr__(name):            # Python calls this only for a name that the module does not define
    return getattr(ibis, name)

def __dir__():
    return sorted(set(globals()) | set(dir(ibis)))
```

The star import copies the names in `ibis.__all__`, for example `_`, `memtable` and `table`. `__getattr__` gives the names that `__all__` does not list. The module adds its own names: `trace`, `visualize`, `render`, `report`, `declare`, `recording`, `load_dbt_model` and `Step`.

### 2.2 The same submodules

`import ibis_anim.selectors as s` needs a module object with the name `ibis_anim.selectors`. The package has no such file. `_SubmoduleAlias` in [`__init__.py:43`](../ibis_anim/__init__.py#L43) is an import hook. The package puts it first in `sys.meta_path`. For each name `ibis_anim.X` that does not start with `_`, the hook does these steps:

1. `find_spec` returns a spec with the hook as its loader.
2. `create_module` imports `ibis.X` and returns that module object. It keeps the real spec of the module in `spec.loader_state`.
3. The import system then writes the alias spec on the module. `exec_module` puts the real spec back.

The result is one module object with two names. The submodules of the library itself (`ibis_anim._tracer` and the others) start with `_`, so the hook does not touch them.

### 2.3 The same classes

The tracer does not wrap the module. It wraps the methods of the ibis classes in place: `ibis.expr.types.Table`, `Join` and `GroupedTable`. `install()` in [`_tracer.py:87`](../ibis_anim/_tracer.py#L87) does this once, at import. After that, each table in the process has the wrapped methods, whatever name the program used for the import.

This is why `python -m ibis_anim script.py` works with a script that keeps `import ibis`. The command imports `ibis_anim`, which patches the classes. Then the script imports `ibis` and gets the same, patched classes.

The real code gives this output:

```python
import ibis
import ibis_anim
import ibis_anim.selectors as s

print(s is ibis.selectors, ibis_anim.Table is ibis.Table, ibis.Table.filter._ibis_anim)
```

```
True True True
```

### 2.4 The same results

The wrapper calls the original method and returns its result unchanged. If the recording of a step fails, the wrapper skips the step and does not raise ([`_tracer.py:111`](../ibis_anim/_tracer.py#L111)). The recording reads ibis expressions only. It does not execute them, because ibis expressions are lazy.

### 2.5 The render at exit

The import registers `_render_at_exit` with `atexit`, and sets the module global `_tracer._steps` to a list. Thus a plain script records each step at module level. When the interpreter exits, the function renders the video, unless the environment variable `IBIS_ANIM` is `0`.

`trace(func)` and `visualize(func)` record into their own list, with the context manager `recording(...)`. `recording(None)` turns the recording off inside a block.

### 2.6 The command line

[`__main__.py`](../ibis_anim/__main__.py) runs a file with `runpy.run_path`:

- Without `:FUNCTION`, it runs the file as `__main__`. The steps go to the list of the script, and the render at exit draws them. If the file defines a dbt-ibis `model`, the command traces that model.
- With `:FUNCTION`, it runs the file inside `recording(None)`, so the module-level steps are not part of the video. Then it calls `trace(func)` and renders the result.

## 3. Block: the method wrapper

**Responsibility.** Record one `Step` for each outermost table call. Do not change the call.

**Toy example.** This example patches a class in the same way as `install()`:

```python
import functools

class Table:                      # stands in for ibis.expr.types.Table
    def __init__(self, ops=()):
        self.ops = ops
    def filter(self, cond):
        return Table(self.ops + (f"filter({cond})",))
    def head(self, n):            # a method that calls another method
        return self.filter("row < %d" % n)

steps = []      # the recording; None would mean "do not record"
depth = 0       # how many wrapped calls are on the stack now

def wrap(name, orig):
    @functools.wraps(orig)
    def wrapper(self, *args):
        global depth
        if steps is None or depth:          # an inner call: do not record it
            return orig(self, *args)
        depth += 1
        try:
            out = orig(self, *args)
            steps.append((name, args, out.ops))
            return out
        finally:
            depth -= 1
    return wrapper

for name in ("filter", "head"):              # patch the class, not the module
    setattr(Table, name, wrap(name, Table.__dict__[name]))

t = Table().filter("x > 1").head(5)
print(steps)
```

```
[('filter', ('x > 1',), ('filter(x > 1)',)), ('head', (5,), ('filter(x > 1)', 'filter(row < 5)'))]
```

The program made three method calls. The recording has two steps, because `head` calls `filter` internally. The depth counter hides that inner call. ibis methods call other ibis methods in the same way, so the real wrapper uses the same counter ([`_tracer.py:97`](../ibis_anim/_tracer.py#L97)).

**The real step.** `_make_step` ([`_tracer.py:121`](../ibis_anim/_tracer.py#L121)) fills a `Step` dataclass:

| Field | Content | Source |
| --- | --- | --- |
| `op` | the method name, or `group_by → aggregate` for a grouped call | the wrapper |
| `inputs` | the receiver table and each table argument | the call arguments |
| `output` | the table that the method returned | the call result |
| `labels` | the variable that holds each input in the calling frame | `_label`, section 4 |
| `code` | the source text of the call | `_call_source`, section 4 |
| `stack` | the user frames, outermost first, as `FrameSummary` objects | `_user_frames`, section 4 |
| `args`, `kwargs` | the call arguments | the call |
| `group_by` | the grouping columns | `receiver.groupings` or the `by` argument |
| `used` | the input columns that the arguments reference | the `Field` nodes of the argument expressions |
| `created` | the output columns that the step adds or overwrites | the output op |

The wrapper keeps the ibis tables themselves. Later blocks compare tables by their op (`table.op()`), because ibis ops are immutable values with equality.

## 4. Block: frame inspection

**Responsibility.** Answer three questions about the call that runs now:

1. Which user functions are on the stack?
2. Which variable holds each input table?
3. What is the source text of the call?

**Toy example.**

```python
import sys
from itertools import islice
import linecache

def where_am_i_called():
    frame = sys._getframe(1)                       # the frame of the caller
    # the variable that holds an object: compare by identity in the caller's locals
    label = next(k for k, v in frame.f_locals.items() if v is target)
    # the exact source span of the call that runs now
    start, end, col, end_col = next(islice(frame.f_code.co_positions(), frame.f_lasti // 2, None))
    line = linecache.getline(frame.f_code.co_filename, start)
    return label, frame.f_code.co_qualname, frame.f_lineno, line[col:end_col]

def pipeline():
    global target
    orders = target = object()
    result = (orders,
              where_am_i_called())
    return result[1]

print(pipeline())
```

```
('orders', 'pipeline', 18, 'where_am_i_called()')
```

The example uses three facts of CPython:

- `sys._getframe(1)` returns the frame of the caller. Each frame has `f_back`, `f_locals`, `f_globals`, `f_code` and `f_lineno`.
- `f_lasti` is the byte offset of the instruction that runs now. Each instruction has 2 bytes, so `f_lasti // 2` is the index of the instruction.
- `co_positions()` (Python 3.11 and later) gives the start line, end line, start column and end column of each instruction. For a call instruction, the span covers the whole call expression.

**The real code.**

- `_user_frames` ([`_tracer.py:228`](../ibis_anim/_tracer.py#L228)) walks `f_back` until it finds the frame of `trace`. It keeps only the frames outside the `ibis_anim` folder, the stdlib, `site-packages` and `<frozen ...>` modules. The trace panel of the video shows this stack as the call path, for example `customer_health › clean()`.
- `_label` ([`_tracer.py:237`](../ibis_anim/_tracer.py#L237)) looks for a variable whose table has the same op as the input, first in the locals and then in the globals of each user frame. If no variable holds the table, the label is the name of an unbound table, or `<op> result` for the output of an earlier step.
- `_call_source` ([`_tracer.py:250`](../ibis_anim/_tracer.py#L250)) cuts the source span with `co_positions`. In a chain such as `a.filter(x).mutate(y)`, the span of the `mutate` call starts at `a`. The function keeps only the text from the last `.mutate(`.

The real tracer gives this output for a small chain. Section 14 shows the full program.

```
filter | ['orders']        | 'orders.filter(_.status == "paid")'
mutate | ['paid']          | 'paid.mutate(tax=_.amount * 0.2)'
filter | ['mutate result'] | 'filter(_.amount > 100)'
```

The third step reads the output of `mutate`, which no variable holds. Its label is therefore `mutate result`, and its code is only the last call of the chain.

## 5. Block: the `ast` module

**Responsibility.** Answer two questions that the runtime objects and the bytecode positions cannot answer.

Two modules import `ast`. Neither one changes or runs the user code. They only parse the source text to find line spans. The library has no import hook that rewrites code, and it does not instrument the bytecode.

| Question | Function | Why the runtime cannot answer it |
| --- | --- | --- |
| Where did the program build a column that a table call reads now? | `_tracer._assignment` | An ibis column expression has no memory of the line that built it. The frame position points to the current call, not to the earlier assignment. |
| Which complete statement holds the current line? | `_scene._statements` | `co_positions` gives the span of one call. A statement can span more lines than the call, for example `revenue = (` on the line above. |

### 5.1 Where was a column built?

This program builds a column outside a table call:

```python
def labels(t):
    label = ibis.coalesce(t.nick, t.player)   # no table method runs here: no step
    return t.mutate(label=label)              # the wrapper sees only this call
```

`coalesce` is not a table method, so the wrapper does not see line 2. When `mutate` runs, `_named_columns` ([`_tracer.py:150`](../ibis_anim/_tracer.py#L150)) finds each argument that is a computed column held by a variable. It finds the name by identity in `f_locals` and `f_globals`, as in section 4. Then `_assignment` ([`_tracer.py:188`](../ibis_anim/_tracer.py#L188)) parses the source of the function and finds the last assignment to that name above the current line.

**Toy example.**

```python
import ast, inspect, sys, textwrap

def last_assignment(frame, name):
    """(line, source of the value) of the last `name = ...` above the current line of `frame`."""
    lines, start = inspect.getsourcelines(frame.f_code)
    source = textwrap.dedent("".join(lines))
    best = None
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            line = node.lineno + start - 1
            if line <= frame.f_lineno and (best is None or line > best[0]):
                best = (line, ast.get_source_segment(source, node.value))
    return best

def report(value):
    caller = sys._getframe(1)
    name = next(k for k, v in caller.f_locals.items() if v is value)
    return name, last_assignment(caller, name)

def pipeline():
    label = "nick or " + \
            "player"            # built here, as a value with no memory of this line
    total = 3
    return report(label)        # read here, two statements later

print(pipeline())
```

```
('label', (21, '"nick or " + \\\n            "player"'))
```

The example uses three parts of `ast`:

- `ast.parse` gives the syntax tree of the function source. `textwrap.dedent` removes the indent of a method, because `ast.parse` rejects an indented first line.
- `node.lineno` is relative to the parsed text. `inspect.getsourcelines` gives the first line of the function in the file. The sum gives the line in the file.
- `ast.get_source_segment` returns the exact text of the value. The text can span more than one line.

The tracer then records a `column` step: a one-column table `t.select(label=...)`, with the line and the text of the assignment. The real tracer gives these steps for `labels`, with their code and labels:

```
column | label = ibis_anim.coalesce(t.nick, t.player) | labels ['t']
mutate | t.mutate(label=label)                       | labels ['t', 'label']
```

The `column` step has the line of the assignment, one line above the line of the `mutate` step. The `mutate` step lists the column table as a second input, so the video can show the column move into the table.

If the source is not available (`OSError`, `TypeError`) or does not parse (`SyntaxError`), `_assignment` returns `None`. The step then uses the current line and the variable name.

### 5.2 Which statement holds the current line?

The code bar at the top of the video shows the whole statement of the current call, with the current line lit. `_statements` ([`_scene.py:339`](../ibis_anim/_scene.py#L339)) gives the first and last line of each statement of a file. `_code_lines` takes the smallest span that holds the current line.

**Toy example.**

```python
import ast

SOURCE = '''\
def model(t):
    revenue = (
        t.filter(t.amount > 0)
        .mutate(tax=t.amount * 0.2)
    )
    if revenue is not None:
        return revenue
'''

def statements(source):
    """(first, last) line of each statement. A compound statement counts its header only."""
    spans = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.stmt):
            body = getattr(node, "body", None)
            last = body[0].lineno - 1 if isinstance(body, list) and body else node.end_lineno
            spans.append((node.lineno, max(last, node.lineno)))
    return spans

def statement_at(source, lineno):
    return min((s for s in statements(source) if s[0] <= lineno <= s[1]), key=lambda s: s[1] - s[0])

print(statements(SOURCE))
print(statement_at(SOURCE, 4))   # the line of `.mutate(...)`
print(statement_at(SOURCE, 6))   # the `if` header, without its body
```

```
[(1, 1), (2, 5), (6, 6), (7, 7)]
(2, 5)
(6, 6)
```

A compound statement (`def`, `if`, `for`, `with`) ends at the line before its body. Without this rule, a call inside a function would show the whole function as its statement. The real function reads the file through `linecache` and caches the result for each file with `functools.cache`.

## 6. Block: table names

**Responsibility.** Give each table one name for the whole video. `table_names` ([`_scope.py:38`](../ibis_anim/_scope.py#L38)) uses three sources, in this order:

1. A name that a loader gave the table with `name_table`. The dbt loader gives each model output the name of its model.
2. The first label that is a Python identifier, that is, a variable of the program.
3. A name from the operation and the names of its inputs, from `_derived_name`. Examples are `filtered orders`, `orders × stores`, `revenue by region` and `paid with tax`.

A derived name always has a space, so it is never an identifier. Other blocks use this rule: `chains()` and the lineage in `_derive.py` treat a name that is an identifier as a variable of the program.

The real code gives these names for the program of section 14:

```
['orders']        -> paid
['paid']          -> paid with tax
['paid with tax'] -> filtered paid with tax
```

The first output gets the variable `paid`. The second output has no variable, so it gets `paid with tax` from `mutate` and its created column. The label of the third step's input was `mutate result`. That label is not an identifier, so the derived name `paid with tax` replaces it.

## 7. Block: chains, calls and scope

**Responsibility.** Group the steps for the three panels of the debugger. All three functions in [`_scope.py`](../ibis_anim/_scope.py) are pure. They read the list of steps and the names, and they return plain data.

| Function | Gives | Rule | Used by |
| --- | --- | --- | --- |
| `chains(steps, names)` | runs of step indexes | Step `i` joins the run of step `i-1` if it reads the output of `i-1` as its first input, in the same function, the output has no variable name, and no later step reads that output. | the pipeline strip, the carry of rows from step to step |
| `calls(steps)` | `Call` objects | Consecutive steps with the same call path form one call. A call `reads` the tables that it did not make. It `hands` on the tables that a later call reads. | the trace panel, the program graph |
| `plan(steps)` | one `ScopeFrame` per step | A table appears when a step reads it first. The output takes the place of its first input if no later step reads that input. A table leaves 2 steps after its last use. | the "tables in scope" shelf |

The real code gives this output for the program of section 14:

```
chains [[0], [1, 2]]
calls [('pipeline()', [0, 1, 2], ['orders'], ['filtered paid with tax'])]
['orders']        -> ['paid']                   | replaced orders
['paid']          -> ['paid with tax']          | replaced paid
['paid with tax'] -> ['filtered paid with tax'] | replaced paid with tax
```

Steps 1 and 2 (`paid.mutate(...).filter(...)`) form one chain. Step 0 is alone, because its output has the variable `paid`. Each output replaces its input on the shelf, because no later step reads the input again. This is the same as a variable rebind, `t = t.filter(...)`.

`plan` reads the future: it knows the last use of each table before it draws the first step. This is possible because the render runs after the program.

## 8. Block: expression substitution

**Responsibility.** Run the expression of a step on different input tables. Four blocks use this one ibis feature:

```python
new_expr = expr.op().replace({old_table.op(): new_table.op()}).to_expr()
```

An ibis expression stores a graph of immutable op nodes. `Node.replace` returns a copy of the graph with some nodes replaced. The recorded step keeps its own expression, so each block can substitute its own inputs.

| Block | Replaces the inputs with | Why |
| --- | --- | --- |
| Facts, `_derive._step` | unbound tables `in0`, `in1` | The SQL of one step reads its direct inputs only, not the full plan above them. |
| Re-run on generated rows, `_scene._binder` | memtables of the rows that `for_step` generated | A schema-only table has no data to read. |
| Re-run, `_scene._run_on_samples` | memtables of the first 6 rows | Each value on the screen comes from the rows on the screen. |
| Part values, `_scene._evaluate` | a memtable of the rows on the screen | The animations of a filter with several tests, of `coalesce` and of `ifelse` show the value of each part on each row. |

**Toy example.**

```python
import ibis
from ibis import _

orders = ibis.memtable({"id": list(range(1, 101)), "amount": [i * 3.0 for i in range(1, 101)]})
step_output = orders.filter(_.amount > 10)          # the expression that the trace recorded

rows = orders.head(6).to_pyarrow()                  # the first rows: the rows on the screen
small = ibis.memtable(rows)
on_screen = step_output.op().replace({orders.op(): small.op()}).to_expr()
print(rows.column("amount").to_pylist())
print(on_screen.to_pyarrow().column("amount").to_pylist())
print(int(step_output.count().execute()), "rows in full")
```

```
[3.0, 6.0, 9.0, 12.0, 15.0, 18.0]
[12.0, 15.0, 18.0]
97 rows in full
```

The full table has 100 rows. The re-run reads 6 of them and keeps 3. The grid title shows both numbers.

## 9. Block: facts

**Responsibility.** Compute what each step guarantees about its result for any data: value sets, nulls, keys, lineage, the row count compared with the input, and warnings. The facts never come from the rows.

The method is an abstract interpretation. The concrete program runs on rows. The abstract program runs on facts about the rows. Each SQL form has a transfer rule that computes the output facts from the input facts.

### 9.1 The abstract domain

[`_facts.py`](../ibis_anim/_facts.py) defines two frozen dataclasses:

| Class | Field | Content |
| --- | --- | --- |
| `Column` | `values` | a `portion` interval: the values that the column can hold. `ANY` means unknown. |
| | `nulls` | `never`, `maybe` or `always` |
| | `equal_to` | source columns with the same set of values, as `"table.column"` |
| | `subset_of` | source columns whose set of values contains this column's set |
| | `fields` | facts of each field of a struct column |
| `TableFacts` | `columns` | column name to `Column` |
| | `keys` | sets of columns that identify a row |
| | `rows` | the row count compared with the input, for example `≤ input` |
| | `warnings` | possible mistakes, as text |
| | `can_empty` | `True` if the step can return no row when its inputs have rows |

One `portion` interval holds a finite set, a range or a complement:

**Toy example.**

```python
import portion as P

ANY = P.open(-P.inf, P.inf)

# facts of the input: what is known for any data, never read from rows
status = P.singleton("paid") | P.singleton("refunded") | P.singleton("trial")
amount = P.closed(0, P.inf)

# transfer rule for WHERE <column> <op> <literal>: intersect with the values that pass
def where(values, op, literal):
    passing = {"=": P.singleton(literal), "!=": ANY - P.singleton(literal),
               ">": P.open(literal, P.inf), "<": P.open(-P.inf, literal)}[op]
    return values & passing

print(where(status, "!=", "trial"))     # the filter removes one value
print(where(amount, ">", 100))          # the range gets a new lower bound
print(where(status, "=", "payed"))      # a typo: no value passes, so the filter keeps no row

# transfer rule for a + b on ranges
def add(a, b):
    return P.closed(a.lower + b.lower, a.upper + b.upper)
print(add(P.closed(0, 10), P.closed(5, 5)))
```

```
['paid'] | ['refunded']
(100,+inf)
()
[5,15]
```

The empty interval `()` of the third line is the source of the warning in section 9.4. The real rules are `_where` and `_condition` in [`_derive.py`](../ibis_anim/_derive.py#L243), and `arithmetic` in [`_facts.py:130`](../ibis_anim/_facts.py#L130). `describe` turns an interval into the text of the video: `{paid, refunded}`, `≥ 0` or `≠ trial`.

### 9.2 From a step to SQL

ibis has more than 20 table methods, and each one has options. SQL has fewer forms: `FROM`, `JOIN`, `WHERE`, `GROUP BY`, the projection, `DISTINCT`, `LIMIT` and the set operations. `_step` ([`_derive.py:75`](../ibis_anim/_derive.py#L75)) compiles each step to DuckDB SQL, and the rules read the SQL. Thus one rule for `WHERE` covers `filter`, `drop_null` and the `WHERE` that other methods produce.

`_step` does these steps:

1. `_frontier` finds the tables with known facts that the output reads. Usually these are the inputs of the step.
2. Each input becomes an unbound table `in0`, `in1` (section 8). The SQL then reads the inputs by name and does not repeat the steps above them.
3. `ibis.to_sql` compiles the expression. `sqlglot.parse_one` parses the SQL.
4. The sqlglot optimizer qualifies the SQL with the schema of the inputs. After this step, each column reference names its table, and each `*` becomes a list of columns. If an optimizer rule fails, `_step` uses `qualify` alone.
5. `_query` walks the tree and applies the rules.

The real code gives this SQL for `orders.filter(_.status == "payed")`, before and after the qualification:

```sql
SELECT
  *
FROM "in0" AS "t0"
WHERE
  "t0"."status" = 'payed'
```

```sql
SELECT "t0"."order_id" AS "order_id", "t0"."status" AS "status", "t0"."amount" AS "amount" FROM "in0" AS "t0" WHERE "t0"."status" = 'payed'
```

### 9.3 The transfer rules

`_select` ([`_derive.py:133`](../ibis_anim/_derive.py#L133)) applies the rules in the order of SQL evaluation:

| SQL form | Rule | Example of a fact |
| --- | --- | --- |
| `FROM in0` | Copy the input facts. Rows `= input`. | |
| `JOIN ... ON a = b` | Intersect the key values. If the right key is unique, the left keys stay keys. A `LEFT JOIN` makes the right columns nullable. | `rows ≤ input`, warning on fan-out |
| `WHERE` | For each conjunct on one column and a literal, intersect the values. `IS NULL` and `IS NOT NULL` change `nulls`. | `status: {paid} · not null` |
| `GROUP BY` | The group columns become the key. | `keys: (order_id)` |
| projection | `_expr` gives the facts of each expression: arithmetic on ranges, `COALESCE`, `CASE`, `CAST`, aggregates, window functions. | `tax: ≥ 0` |
| `DISTINCT` | All columns form a key. | |
| `LIMIT n` | Rows `≤ n`. `LIMIT 1` makes each column a key. | `rows ≤ 1` |
| `UNION`, `INTERSECT`, `EXCEPT` | `_set_operation` takes the union or intersection of the value sets. | |

A step that removes rows calls `Column.narrowed()`. A column that was `equal_to` a source column becomes `subset_of` it, because some values can be gone.

### 9.4 Soundness and warnings

Each fact over-approximates: the real values are always inside `values`. If a rule does not know a form, it gives `ANY` or no fact. If `_step` cannot read a step at all, the step gets no facts and the warning `no facts for this step`. So a fact can be missing, but it is never wrong.

The rules write a warning when a fact shows a possible mistake:

- a filter that keeps no row
- a join that can repeat rows, because the right key is not known to be unique
- join keys with no value in common
- a division by a column that can be 0
- a cast that drops the fraction of a value

The real code gives this report for the typo of section 9.2. `orders` declares the values `{paid, refunded}` for `status`.

```
 1. typo() › filter(orders) → filtered orders   rows ≤ input · can be empty
      keys: (order_id)
      order_id: unique · not null · ⊆ orders.order_id
      status: ∅ (no value) · not null · ⊆ orders.status
      amount: ⊆ orders.amount
      ⚠ status has no possible value after `status = 'payed'`: the filter keeps no row
```

### 9.5 Declarations

The input facts come from two sources:

- **The schema.** A non-nullable ibis type gives `nulls = never`.
- **Declarations.** `declare(table, key=..., values=..., not_null=..., references=..., unique=...)` stores constraints in the module dict `_facts._declared`, keyed by the op of the table. `source_facts` reads them for a source table. `with_declared` adds them to the output of a step, for example the dbt tests of a model output.

A declaration must state a rule that holds for any data, such as an enum type, a `CHECK` constraint or a dbt test. It must not state values read from a sample.

### 9.6 Lineage

`derive` ([`_derive.py:32`](../ibis_anim/_derive.py#L32)) adds `"name.column"` to `equal_to` for each input table that has a variable name. Unnamed results of a chain do not add an entry. The dict `ranks` stores the step where each origin began. `column_text` uses it to show the most upstream origin, for example `⊆ orders.status`. The origin then stays the same from step to step until the relation changes.

## 10. Block: sample rows for schema-only tables

**Responsibility.** Make rows for each table that has a schema only, `ibis.table(schema, name=...)`. The video needs rows, but the facts do not use them.

`for_step(step, facts)` ([`_samples.py`](../ibis_anim/_samples.py)) makes about 6 rows for each schema-only table that one step reads. Each step gets its own rows: rows made once for the source tables do not survive the filters and joins of a long pipeline. The tables are the frontier of the step: usually its inputs, but a chained join reads the tables of the joins before it.

1. **Facts of the input.** The facts of each table (section 9) hold what the steps before guarantee. The value of a row is inside the value set of its column, a key gets distinct values, and a column that is never null gets no null. After `filter(_.status == "paid")`, the next step gets rows with status `paid` only.
2. **Tests of the step.** `_tests` turns each test of a column into the interval of the values that pass it: `amount > 100` gives `(100, +inf)`. `_patterns` decides which tests each row passes: two rows pass all, one row fails each test on its own, and one row fails all. `_tested` picks a value inside the facts and on the side of each test that the row needs.
3. **Join keys.** `_link` gives the right key every second value of the left key, and new values in between. A right key that is not unique repeats a value, so the join fans out.
4. **Other arguments.** Group keys, window partitions and `names_from` repeat with a period of 2 or 3. A distinct gets duplicate rows. A set operation gets rows that are in both tables. `drop_null`, `fill_null` and `coalesce` get nulls.
5. **Computed columns.** `_recomputed` computes again each column that a mutate before the table computes from other columns of the table. Thus `big = amount > 200` agrees with `amount` in each row.
6. **Types.** The other values come from the type: `A, B, C` for strings, `1, 2, 3` for integers, dates from 2024-01-01.

A step that casts a text column to another type gets tables without rows: no generated text is sure to parse. The video shows the change of the types, and the facts give the warning `cast of x to BIGINT fails on a text that is not a BIGINT`.

The rows for `paid.join(customers, "customer_id")`, after `orders.filter((_.status == "paid") & (_.amount > 100))`, with `customer_id` declared as the key of `customers`:

```
paid      {'customer_id': [1, 2, 3, 4, 5, 6], 'amount': [150.0, 200.0, 250.0, 120.0, 310.0, 199.9],
           'status': ['paid', 'paid', 'paid', 'paid', 'paid', 'paid'], ...}
customers {'customer_id': [1, 8, 3, 10, 5, 12], 'region': ['A', 'B', 'C', 'A', 'B', 'C'], ...}
```

Each value of `paid` agrees with the filter before it, and half of the keys match.

## 11. Block: dbt-ibis models as input

**Responsibility.** Give a dbt-ibis `model` function its input tables, and run the models upstream of it.

A dbt-ibis model declares its inputs with `@depends_on(ref("..."), source("...", "..."))`. `trace(model)` without arguments calls `_dbt.run` ([`_dbt.py:37`](../ibis_anim/_dbt.py#L37)). `Project` finds the nearest folder with `dbt_project.yml`. It reads the model files and the YAML files. For each reference, `Project.table` gives a table:

- A `ref` to an ibis model (`.py` or `.ibis`) runs that model first, with its own references, under the same recording.
- A `ref` to a SQL model, or a `source`, gives a schema-only table. The types come from `data_type` in the YAML. Section 10 makes its rows.

The dbt tests `unique`, `not_null` and `accepted_values`, and the constraints `primary_key` and `not_null`, become `declare` facts. `name_table` gives each model output the name of its model. `_rename` changes the `co_name` of the `model` function to the model name, so the trace shows `stg_stores()` and not `model()`.

## 12. Block: the re-run on the rows on the screen

**Responsibility.** Give each animation the input rows and the output rows that it draws, so that each value on the screen comes from the rows on the screen.

`_run_on_samples` ([`_scene.py:127`](../ibis_anim/_scene.py#L127)) does these steps for one step:

1. Take the first 6 rows of each input. A schema-only table reads the rows that `for_step` generated for this step (section 10).
2. Replace each input with a memtable of its rows (section 8).
3. Execute the replaced output on DuckDB. Keep at most 8 result rows.
4. Return a `Sample` for each input and one for the output. A `Sample` holds the label, the columns, the rows, the full row count, the column types and the facts text.

In a chain, a step reads the rows that the step before it showed, not a new sample of the full table. The scene passes those rows as `given`. The `Sample` then has `partial=True`, and the grid title says `of the rows shown`.

## 13. Block: the scene

**Responsibility.** Draw the debugger and animate each step. [`_scene.py`](../ibis_anim/_scene.py) is the largest module, with about 2000 lines. It has four layers: manim, the visual parts, the step loop and the choreographies.

### 13.1 manim in four ideas

1. A `Scene` subclass draws in its `construct` method.
2. A mobject is a drawable object. `Text`, `Rectangle` and `VGroup` (a group of mobjects) are examples.
3. `self.play(animation, ...)` renders the frames of the animations at the same time. `mobject.animate.shift(...)` turns a change of a mobject into an animation.
4. `self.next_section(name)` starts a new section. With `save_sections`, each section goes to its own clip.

**Toy example.** This scene shows the idea of the filter animation in 22 lines:

```python
from manim import DOWN, RED, UP, FadeIn, FadeOut, Rectangle, Scene, Text, VGroup, tempconfig

ROWS = [{"id": 1, "amount": 120}, {"id": 2, "amount": 40}, {"id": 3, "amount": 300}]
KEEP = [row["amount"] > 100 for row in ROWS]   # the result of the step on the rows on the screen

def cell(text):
    box = Rectangle(width=1.4, height=0.5).set_fill("#1c1c1c", 1)
    return VGroup(box, Text(str(text), font_size=22).move_to(box))

class FilterScene(Scene):
    def construct(self):
        rows = VGroup(*[VGroup(cell(r["id"]), cell(r["amount"])).arrange(buff=0) for r in ROWS]).arrange(DOWN, buff=0)
        self.next_section("01_filter")                       # one section: one clip per step
        self.play(FadeIn(rows))
        gone = [row for row, keep in zip(rows, KEEP) if not keep]
        self.play(*[row[k][0].animate.set_fill(RED, 0.7) for row in gone for k in range(2)])
        self.play(*[FadeOut(row, shift=DOWN * 0.3) for row in gone])
        kept = [row for row, keep in zip(rows, KEEP) if keep]
        self.play(*[row.animate.move_to(rows[0].get_center() + DOWN * 0.5 * k) for k, row in enumerate(kept)])

with tempconfig({"quality": "low_quality", "media_dir": "media", "save_sections": True, "verbosity": "WARNING"}):
    FilterScene().render()
```

The render takes about 3 seconds and writes these files, among others:

```
media/videos/480p15/FilterScene.mp4
media/videos/480p15/sections/FilterScene_0000_01_filter.mp4
```

The real filter has the same phases: show the rows, mark the rows that fail, remove them, slide the other rows up. It adds a ✔ or ✘ mark on each row, the condition label and the caption of each phase.

### 13.2 The visual parts

| Class or method | Draws | Notes |
| --- | --- | --- |
| `Grid` ([`_scene.py:427`](../ibis_anim/_scene.py#L427)) | a table on the stage | `cells` maps `(row, column)` to a cell. Row `-1` is the header, `-2` the types, `-3` the facts. Rows `0..n` hold the data. |
| `Card` ([`_scene.py:499`](../ibis_anim/_scene.py#L499)) | a table on the shelf | Name, shape and a mini table. `set_state` gives the look for `input`, `output`, `idle` or `dead`. |
| `_header` | the code bar, the trace panel, the pipeline strip and the program graph | It uses `_code_lines` (section 5.2), `calls` and `chains` (section 7). |
| `_say` | the caption of the current phase | |
| `_guarantees` | the facts that changed, and the warnings | It uses the facts of section 9. |

### 13.3 The step loop

`DebuggerScene.__init__` runs the render-phase blocks once: `plan`, `table_names`, `derive`, `generate` and `bind`, `chains` and `calls`. `construct` ([`_scene.py:598`](../ibis_anim/_scene.py#L598)) then runs five methods for each step, in one section per step:

```python
for i, (step, frame) in enumerate(zip(self.steps, self.frames)):
    self.next_section(f"{i + 1:02d}_{step.function}_{step.op.split()[-1]}")
    self._header(i, step)        # code bar, trace, pipeline strip, program graph
    self._activate(frame)        # shelf cards: inputs light up, unused tables dim
    self.result = None
    self._animate(step)          # the choreography of the verb
    self._guarantees(step)       # facts and warnings
    self.wait(1.5)
    self._finish(i, step, frame) # the result goes to the shelf, or stays on the stage
```

### 13.4 The choreographies

`_animate` ([`_scene.py:1083`](../ibis_anim/_scene.py#L1083)) calls `_run_on_samples` (section 12). Then it sends the step to the choreography of its verb:

| Verb | Method | Phases |
| --- | --- | --- |
| `filter` | `_filter`, `_subset`, `_tests` | Mark each row with ✔ or ✘. With several tests, each test gets a column of marks, and the marks of each row merge with AND or OR. Remove the failed rows. |
| `limit`, `head`, `sample` | `_subset` | A cut line, then remove the rows below it. |
| `join` | `_join` | Move the keys to the inner edges. Bring the tables together. Line up equal keys. Merge the key columns. |
| `aggregate`, `agg` | `_aggregate` | Colour the rows by group. Gather each group. Collapse each group into one row. |
| `order_by` | `_order_by` | Mark the sort keys and their direction. Move each row along an arc to its new place. |
| `mutate`, `select`, `drop`, `rename`, `relocate`, `cast`, `try_cast`, `column` | `_columns` | Carried columns keep or change their place. For each row, the source cells merge into each new cell. |
| a window function | `_window` | Colour the partitions. Sort the rows. Slide the window frame down the rows. |
| `distinct`, `drop_null`, `fill_null` | `_distinct`, `_drop_null`, `_fill_null` | |
| `union`, `intersect`, `difference` | `_set_op` | |
| `pivot_wider`, `pivot_longer`, `unnest` | `_pivot_wider`, `_pivot_longer`, `_unnest` | |
| other | `_flow` | Each output column comes from the input column with the same name. |

If a choreography raises an exception, `_animate` prints `plain animation` on stderr, clears the stage and draws the step with `_flow`. A new verb therefore needs no choreography to appear in the video.

### 13.5 Continuity between steps

Three mechanisms keep the video readable from one step to the next:

- **The carry.** If the next step reads the result as its first input, `_finish` ([`_scene.py:948`](../ibis_anim/_scene.py#L948)) keeps the result grid on the stage as `self.carry`. The next `_enter` calls `_morph`, which moves each cell to its new place. `_morph` matches the rows by their values.
- **Persistent mobjects.** `_persistent()` lists the mobjects that stay on the screen: the frame, the panels, the code bar, the caption, the carry and the cards. `_settle` and `_finish` remove all other mobjects.
- **The layout per step.** The code bar has the height of the current statement. `_layout(i)` computes the positions of the parts below it for each step.

### 13.6 The render

`render` ([`_scene.py:77`](../ibis_anim/_scene.py#L77)) does these steps:

1. Set the resolution from `quality` or `IBIS_ANIM_QUALITY`: `low` (480p), `medium` (720p) or `high` (1080p, the default).
2. Make a new subclass of `DebuggerScene` with `type(name, (DebuggerScene,), {})`. manim names its folders after the scene class, so two videos can render at the same time. Each video also has its own text cache.
3. Turn off the recording with `recording(None)`. The scene calls wrapped ibis methods, for example `head`, and these calls must not become steps.
4. Render the scene, then copy the video to `media/<name>.mp4`. The clip of each step stays in `media/videos/<resolution>/sections/`.

## 14. One program from start to end

This program has three steps in one function. `declare` states the key and the value sets of `orders`.

```python
import ibis_anim as ibis
from ibis_anim import _

orders = ibis.memtable({"order_id": [1, 2, 3, 4], "status": ["paid", "trial", "paid", "refunded"],
                        "amount": [120.0, 0.0, 40.0, 75.0]})
ibis.declare(orders, key="order_id", values={"status": {"paid", "refunded", "trial"}, "amount": (0, None)})

def pipeline(orders):
    paid = orders.filter(_.status == "paid")
    big = paid.mutate(tax=_.amount * 0.2).filter(_.amount > 100)
    return big

result, steps = ibis.trace(pipeline, orders)
print(ibis.report(pipeline, orders))
```

Each block adds one layer of data to the three steps:

| Block | Step 1 | Step 2 | Step 3 |
| --- | --- | --- | --- |
| Wrapper (3) | `filter` | `mutate` | `filter` |
| Frames (4) | label `orders` | label `paid` | label `mutate result`, code `filter(_.amount > 100)` |
| Names (6) | output `paid` | output `paid with tax` | output `filtered paid with tax` |
| Chains (7) | chain `[0]` | chain `[1, 2]` | chain `[1, 2]` |
| Plan (7) | `paid` replaces `orders` | `paid with tax` replaces `paid` | the output replaces `paid with tax` |
| Facts (9) | `status: {paid}`, rows `≤ input` | `tax: ≥ 0`, rows `= input` | `amount: > 100 · not null` |
| Re-run (12) | 2 of 4 rows stay | 2 rows, one new column | 1 row stays, from the 2 rows of step 2 |
| Scene (13) | `_subset` with ✔ and ✘, the result stays as the carry | `_columns`, the result stays as the carry | `_subset` on the carried grid |

The real report for this program:

```
 1. pipeline() › filter(orders) → paid   rows ≤ input · can be empty
      keys: (order_id)
      order_id: unique · not null · ⊆ orders.order_id
      status: {paid} · not null · ⊆ orders.status
      amount: ≥ 0 · ⊆ orders.amount
 2. pipeline() › mutate(paid) → paid with tax   rows = input
      keys: (order_id)
      order_id: unique · not null · = paid.order_id · ⊆ orders.order_id
      status: {paid} · not null · = paid.status · ⊆ orders.status
      amount: ≥ 0 · = paid.amount · ⊆ orders.amount
      tax: ≥ 0
 3. pipeline() › filter(paid with tax) → filtered paid with tax   rows ≤ input · can be empty
      keys: (order_id)
      order_id: unique · not null · ⊆ orders.order_id
      status: {paid} · not null · ⊆ orders.status
      amount: > 100 · not null · ⊆ orders.amount
      tax: ≥ 0
```

The fact `tax: ≥ 0` comes from the range arithmetic: `amount ≥ 0` times the literal `0.2`. Step 3 narrows `amount` from `≥ 0` to `> 100`. The comparison also removes the nulls.

## 15. Known limits

The code marks each deliberate simplification with a `ponytail:` comment. These comments give the limits below:

- **One thread.** The recording uses the module globals `_steps` and `_depth` ([`_tracer.py:59`](../ibis_anim/_tracer.py#L59)). Two threads that call ibis at the same time mix their steps.
- **Skipped steps.** If the wrapper cannot record a call, for example an unfinished join, it skips the step and does not raise.
- **Missing facts.** A SQL shape that the rules do not read gives no facts for that step. This result is sound, but the video shows less.
- **Plain animation.** An operation shape that a choreography does not handle gets the `_flow` animation.
- **Long names.** A derived name longer than 40 characters is cut, not rephrased.
