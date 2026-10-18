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

`generate` in `ibis_anim/_samples.py` makes 6 rows for each schema-only source table. It collects hints from the trace first.

1. To add a hint from the code, add a loop over the op type in the loop over `_own_values(step.output.op())`. Find the source column with `source_columns(field.rel, field.name)`, and append `(op type, literal value)` to `hints[source column]`.
2. To change the values for a hint, change `_around`. For each test on a literal, give values that pass and values that fail.
3. To change the values of a type, change `_base`.
4. Add a check in `tests/test_samples.py`. Assert on the generated values, and that each test of the code has rows that pass and rows that fail.

The sample rows are for the video only. The facts must not use them.

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
