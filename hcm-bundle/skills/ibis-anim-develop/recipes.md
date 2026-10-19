# Recipes for ibis-anim features

Each recipe names the files to change, in order, and the check to add. The paths are relative to the root of the ibis-anim clone. Run all checks after each recipe, as the workflow in [the skill](./SKILL.md) says.

## Record a new method

Do this when the tracer does not wrap a table method. Such a method gives one of two symptoms:

- The method calls no wrapped method, and the trace has no step for it. An example is `view`.
- The method calls wrapped methods internally, and the trace shows those inner calls as steps with empty code. For example, `t.topk(1)` gives three steps: `group_by → agg`, `order_by` and `limit`.

The tracer does not wrap `topk`, `asof_join` or `window_by`, for example. `TABLE_METHODS` lists the wrapped methods.

1. Add the method name to `TABLE_METHODS` in `ibis_anim/_tracer.py`. For a method of `GroupedTable`, add it to `GROUPED_METHODS`.
2. Make sure that the class defines the method itself. `install()` wraps only a name in `cls.__dict__`. A method that the class inherits from a base class is not wrapped. If ibis defines the method on a base class, add that class to the loop in `install()`.
3. Check the fields of the step: `labels`, `used` and `created`. `_make_step` reads `used` from the `Field` nodes of the arguments. It reads `created` from the values of a `Project` op, or from the new output columns.
4. Add a check in `tests/test_tracer.py`:

   ```python
   def test_trace_records_topk():
       t = ibis_anim.memtable({"a": [1, 2, 2]})
       _r, steps = ibis_anim.trace(lambda: t.topk(1))   # do not name the result `_`: it hides the ibis `_`
       assert [s.op for s in steps] == ["topk"]         # today: ["group_by → agg", "order_by", "limit"]
   ```

5. Give the result a name. See [Name the result](#name-the-result-of-an-operation).
6. Check the facts: `print(ibis_anim.report(func))`. If the step shows `no facts for this step`, see [Add a facts rule](#add-a-facts-rule).
7. Without a choreography, the step uses `_flow`. To give it its own animation, see [Animate an operation](#animate-an-operation).

After the change, the depth counter records only the outer call, and the inner calls of ibis are not steps. The same rule applies today to `left_join`, which calls `join`: the trace shows one `join` step.

## Name the result of an operation

`_derived_name` in `ibis_anim/_scope.py` names a result that no variable holds.

1. For a name of the form "<words> <first input> <words>", add the verb to `_PHRASES`. `{}` is the name of the first input:

   ```python
   "topk": "top values of {}",
   ```

2. For a name that needs the arguments or the second input, add a branch in `_derived_name`. Read the arguments from `step.args` and `step.kwargs`.
3. The name must have a space. A name without a space is read as a variable of the program by `chains()` and by the lineage of the facts.
4. A name longer than 40 characters is cut. Keep phrases short.
5. Add a check in `tests/test_scope.py` that compares `table_names(steps)[steps[0].output.op()]` with the expected name.

## Animate an operation

A choreography is a method of `DebuggerScene` in `ibis_anim/_scene.py`. It draws one step on the stage.

1. Add a branch for the verb in `_animate`. The order of the branches matters: the first match wins. A step with a window function goes to `_window` before `_columns`.
2. Write the method. It receives the step, a `Sample` for each input and a `Sample` for the output. Each sample already holds the first rows, the facts text and the name of its table. This skeleton renders correctly:

   ```python
   def _distinct(self, step: Step, ins: list[Sample], out: Sample):
       """Show the input, mark the columns that the step reads, then build the result."""
       s = ins[0]
       width = _cell_width([s, out], [*s.columns, *out.columns])
       inp = Grid(s, _pick_columns(s.columns, step.used), width, used=step.used)
       res = Grid(out, _pick_columns(out.columns, step.created), width, created=step.created)
       self._side_by_side([inp, res])          # scale and place the grids on the stage
       self._enter(inp)                        # grow from the shelf card, or move the carried table
       self._say("compare the rows on all columns")
       self._highlight(inp, inp.shown)
       self._say("each distinct row goes to the result once")
       self.play(FadeIn(res.title), LaggedStart(*[TransformFromCopy(inp.column(c), res.column(c)) for c in res.shown], lag_ratio=0.2))
       self._settle(inp, res)                  # remove everything else from the stage
       self.result = res                       # `_guarantees` and `_finish` use it
   ```

3. Use the helpers of the scene:

   | Helper | Use |
   | --- | --- |
   | `_side_by_side(grids)`, `_in_place(grid, ...)` | Scale and place grids. `_in_place` draws a step that changes its input in place, such as a filter. |
   | `_enter(*grids)` | Bring the input grids onto the stage. Call it once, after the placement. |
   | `_say(text)` | Show the caption of one phase. Write one caption for each phase. |
   | `_highlight`, `_fly`, `_drop`, `_reorder`, `_tint` | Mark columns, move cells, remove rows, reorder rows, colour rows by key. |
   | `_match(outs, ins, pairs)` | Find the input row of each output row by equal values. |
   | `_retitle(grid, out)`, `_refresh_facts(grid, out)` | At the end of an in-place step, give the input grid the title and the facts of the result. |

4. End the method with `self._settle(...)` and `self.result = <the result grid>`. `_finish` moves `self.result` to the shelf, or keeps it on the stage for the next step.
5. If the method cannot draw a shape of call, raise an exception. `_animate` then draws the step with `_flow` and prints `plain animation` on stderr. Do not draw a wrong animation.
6. Follow the rules of the scene:
   - The height of the top bar changes with each statement. Read `self.zone_top`, `self.caption_y`, `self.strip_y` and `self.bar_bottom`. Do not use the module constants `ZONE_TOP`, `CAPTION_Y`, `STRIP_Y` and `BAR_BOTTOM`.
   - Add each mobject that must stay on the screen after the step to `_persistent()`. `_settle` and `_finish` remove all other mobjects.
   - manim `Text` drops leading spaces. To indent a line, move it by the indent times `_char_width(...)`.
7. Render an example at low quality and read its frames. Check the end state of the step, and check that no text overlaps.
8. Add a row for the operation to the animation table in `README.md`.

## Add a facts rule

`ibis_anim/_derive.py` computes the facts from the SQL of each step. A rule must be sound: the real values must always be inside the computed `values`. If a rule is not certain, it returns `Column()`, which means "any value, maybe null".

1. Find the SQL form of the step. Compile the step with `ibis.to_sql(expr, dialect="duckdb")`, and parse it with `sqlglot.parse_one(sql, read="duckdb")`.
2. Choose the function:

   | SQL form | Function |
   | --- | --- |
   | an expression in the projection | `_expr` |
   | `FROM`, `GROUP BY`, `DISTINCT`, `LIMIT`, `UNNEST` in the projection | `_select` |
   | `JOIN` | `_join` |
   | `WHERE` | `_where` and `_condition` |
   | `UNION`, `INTERSECT`, `EXCEPT` | `_set_operation` |
   | an aggregate, a window function, a cast | `_aggregate`, `_window`, `_cast` |

3. Write the rule. For example, `t.mutate(y=_.x.abs())` compiles to `ABS("t0"."x")`, which sqlglot parses as `exp.Abs`. Today the column `y` gets no facts. This branch in `_expr` gives `y: ≥ 0`:

   ```python
   if isinstance(e, exp.Abs):
       a = sub(e.this)                          # the facts of the argument
       return Column(P.closed(0, P.inf), a.nulls)
   ```

   Use `safe(...)` around a set operation on values that can have different types. Use `arithmetic` from `_facts.py` for `+`, `-` and `*` on ranges.
4. To report a possible mistake, append a sentence to the `warnings` list of the rule. Write the placeholder table names `in0` and `in1` as they are. `_relabel` replaces them with the names of the tables.
5. Add a check in `tests/test_facts.py` with the helper `facts_of`:

   ```python
   def test_abs_is_never_negative():
       absolute, = facts_of(lambda: orders.mutate(y=_.amount.abs()))
       assert absolute.column("y").values == P.closed(0, P.inf)
   ```

6. Do not read values from the rows. A fact that holds for the sample rows only is wrong for other data.

## Improve the sample rows

`for_step(step, facts, computed)` in `ibis_anim/_samples.py` makes about 6 rows for each schema-only table that one step reads. Each step gets its own rows. The values stay inside the facts of each input, so the rows agree with the steps before.

1. To test a new kind of condition, extend `_tests`. It turns each test of a column into `((table, column), values that pass, whether null passes)`. `_patterns` and `_tested` then give each test rows that pass and rows that fail.
2. To change the values near a test, change `_around`. To change the values of a type, change `_base`.
3. For an argument that needs a row shape, for example repeated keys or nulls, extend `_repeated` or `_null_columns`, or add a branch for the verb at the end of `for_step`. Do not break a fact: a declared key stays unique.
4. A join reads keys from `_join_keys`. It links each join of a chained join, in order. `_link` gives the right key every second value of the left key.
5. Columns that an earlier mutate computes, and filters before the table, go through `_below` and `_recomputed`. Each value reads the generated columns, with the formula of an earlier computed column in place of the column, so one flat query runs them all. Do not build a chain of `mutate` calls: it compiles slowly on a long pipeline.
6. Add a check in `tests/test_samples.py`. Run the step on its rows with the `run` helper of that file. Assert on the rows of the step after the change too, because a wrong row often shows one step later.

The sample rows are for the video only. The facts must not use them.

## Keep long pipelines fast

The render time of the data must grow with the number of steps, not faster.

- Use `_derive._substitute`, not `Node.replace`. `replace` walks the whole plan below the expression at each call.
- Read the rows and counts of a step on real data through the computed tables: pass `self.computed` to `_run_on_samples`, `_plain_samples` and `for_step`.
- Make a memtable of DuckDB rows with `_scene._memtable`. It copies the rows, so that DuckDB buffers do not stay alive until exit.
- To measure, trace a long synthetic pipeline (80 steps or more) and time the data path without manim. Count the backend calls: each step must run a fixed number of queries.

## Check the numpy 1.x stack

ibis-anim supports two stacks. Run this check after a change to `pyproject.toml`, after a change that uses a new manim, numpy or pyarrow function, and before a release.

| Stack | Python | numpy | pyarrow | manim | FFmpeg programs |
| --- | --- | --- | --- | --- | --- |
| current | 3.11 or later | 2.x | latest | latest (0.22) | not needed |
| numpy 1.x | 3.11 or 3.12 | 1.26 | 14.0.2 | 0.18.1 | `ffmpeg` and `ffprobe` |

The user guide for the numpy 1.x stack is [the numpy 1.x setup](../ibis-anim/numpy1.md) of the `ibis-anim` skill.

Rules for code that must run on both stacks:

- Use only manim features that manim 0.18.1 has. For example, sections, `tempconfig` and the config key `disable_caching` exist in 0.18.1.
- Do not use numpy 2 functions, and do not use pyarrow functions newer than pyarrow 14.
- Do not import a module for the first time inside the render at exit. On Python 3.11, an import that registers a thread hook then raises `can't register atexit after shutdown`. `__init__.py` imports `concurrent.futures.thread` first for this reason, and `test_the_render_at_exit_can_import_a_thread_pool` in `tests/test_tracer.py` checks it. That check fails on Python 3.11 only.

### Make the test environment

1. Make a Python 3.11 environment outside the clone. On Windows, keep the path of the environment short. ibis has test files with long paths, and a long folder name makes pip fail with `[WinError 206] The filename or extension is too long`. If the folder must be long, map it to a drive letter first, in PowerShell or cmd:

   ```powershell
   subst X: C:\a\long\folder\for\the\environment    # remove it later with: subst X: /d
   py -3.11 -m venv X:\v
   ```

   Run `subst X: /d` in PowerShell or cmd, not in Git Bash: Git Bash changes `/d` into a path.

2. Install the clone with the numpy 1.x set. Give the clone as a path, so that pip reads its `pyproject.toml`:

   ```sh
   X:/v/Scripts/python -m pip install "C:/path/to/ibis-anim[dbt]" "pyarrow==14.0.2" "numpy<2"
   X:/v/Scripts/python -m pip check
   X:/v/Scripts/python -m pip list    # manim 0.18.1, numpy 1.26.x, pyarrow 14.0.2
   ```

   If pip does not find manim 0.18.1, a requirement in `pyproject.toml` excludes it. Fix `pyproject.toml`, not the test.

3. Check that pip still resolves the set from `pyproject.toml`, without the environment:

   ```sh
   X:/v/Scripts/python -m pip install --dry-run --ignore-installed --quiet --report report.json "C:/path/to/ibis-anim" "pyarrow==14.0.2" "numpy<2"
   ```

   Read the versions in the `install` list of `report.json`. Write the report to a file: on stdout, it mixes with other output. Without `numpy<2`, the report gives numpy 2 and manim 0.22. That is expected, and it is the reason why the user guide tells projects to add `numpy<2`.

### Run the checks

Set `PYTHONPATH` to the clone, so that the checks import the code of the clone, not the copy that pip installed:

```sh
export IBIS_ANIM=0 PYTHONIOENCODING=utf-8 PYTHONPATH=C:/path/to/ibis-anim
for t in tests/test_*.py; do X:/v/Scripts/python "$t"; done
```

Each file must print `ok`. The import of manim 0.18 warns `Couldn't find ffmpeg or avconv` when `ffmpeg` is not on the PATH. The checks do not need it.

### Check a render

manim 0.18 needs the `ffmpeg` and `ffprobe` programs on the PATH. [External programs](../ibis-anim/numpy1.md#external-programs) in the user guide lists how to install them on each system, how to put them on the PATH and how to check the PATH. With a full FFmpeg on the PATH, render an example at low quality from an empty folder:

```sh
IBIS_ANIM_QUALITY=low X:/v/Scripts/python -m ibis_anim C:/path/to/ibis-anim/examples/schema_only.py
```

Without FFmpeg, use a test setup:

1. Install `imageio-ffmpeg` and `av` in the test environment. `imageio-ffmpeg` has an `ffmpeg` binary, but no `ffprobe`.
2. Copy the binary, `python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"`, to an empty folder as `ffmpeg.exe` (`ffmpeg` on other systems). Put that folder first on the PATH. In Git Bash, write the folder as `/c/...`: a `C:/...` entry breaks the PATH at its colon.
3. Render through [the manim 0.18 script](./manim018.py), which reads the clip metadata with PyAV instead of `ffprobe`. It takes the same arguments as `python -m ibis_anim`:

   ```sh
   IBIS_ANIM_QUALITY=low PYTHONPATH=C:/path/to/ibis-anim X:/v/Scripts/python manim018.py C:/path/to/ibis-anim/examples/table_ops.py
   ```

For each render, the last line must be `ibis_anim: video at ...`, and no line must say `plain animation` or `Traceback`. Render one example on real data (`table_ops.py`) and one schema-only example (`schema_only.py`). Then compare their frames with a render on the current stack.

## Read a dbt test

`Project._tests` in `ibis_anim/_dbt.py` turns the tests and constraints of the YAML columns into the arguments of `declare`: `unique`, `not_null` and `values`.

1. Add a branch for the test kind in the loop over `checks`.
2. Give it an argument of `declare` that states the same constraint. If no argument of `declare` states it, extend `declare` in `_facts.py` first.
3. Add a check in `tests/test_dbt.py` with a YAML file that uses the test.

The `relationships` test needs the referenced table. `declare` takes it as `references={column: table.column}`. The loader must create the referenced table first.

## Add a public function

1. Write the function in the module of its block. The name of a new module file must start with `_`. `_SubmoduleAlias` in `__init__.py` sends each import of `ibis_anim.<name>` without `_` to `ibis.<name>`, so a public module name is not reachable.
2. Import the function in `ibis_anim/__init__.py`, and add its name to `__all__`.
3. Make sure that the name is not a name of `ibis`. `ibis_anim` re-exports all names of `ibis`, and a new name hides the ibis name.
4. Add a check in the test file of the block, and document the function in `README.md`.
