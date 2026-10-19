# The architecture of ibis-anim

The library has two phases:

1. **Record.** This phase runs while the program runs. A wrapper on each ibis table method records one `Step` for each outermost call. No data is read.
2. **Render.** This phase runs at exit, on the complete list of steps. All other blocks run in this phase. They know the future of each table, for example the step that reads it last.

The data goes through five stages: trace → names and scope → facts → sample rows for each step → scene.

```
user code ─► wrapped methods ─► list[Step] ─┬─► table_names ─► chains / calls / plan ─┐
             _tracer.py                     ├─► derive ─► TableFacts per table ───────┤
                                            └─► _materialize ─► a DuckDB table ───────┤
                                                 for each step on real data           ▼
                                              DebuggerScene: for each step, for_step makes rows for
                                              schema-only tables, re-run on 6 rows, animate
```

## The building blocks

| Block | File and function | Reads | Gives | Test file |
| --- | --- | --- | --- | --- |
| Drop-in front | `__init__.py` | the `ibis` module | the same names and submodules as `ibis` | `test_tracer.py` |
| Command line | `__main__.py`, `main`, `dbt` | a file path, or `dbt` and a model | a video | `test_dbt.py` |
| Method wrapper | `_tracer.py`, `install`, `_wrap`, `_make_step` | ibis calls | `Step` | `test_tracer.py` |
| Frame inspection | `_tracer.py`, `_user_frames`, `_label`, `_call_source` | Python frames | `Step.stack`, `labels`, `code` | `test_tracer.py` |
| Column steps | `_tracer.py`, `_named_columns`, `_assignment` | the source of the calling function, through `ast` | `column` steps | `test_tracer.py` |
| Table names | `_scope.py`, `table_names`, `_derived_name` | steps | op → name | `test_scope.py` |
| Chains, calls, scope | `_scope.py`, `chains`, `calls`, `plan` | steps, names | runs of steps, `Call`, `ScopeFrame` | `test_scope.py` |
| Facts domain | `_facts.py`, `Column`, `TableFacts`, `declare` | declarations, schemas | input facts | `test_facts.py` |
| Facts rules | `_derive.py`, `derive`, `_step`, `_select`, `_expr` | steps, names, input facts | op → `TableFacts` | `test_facts.py`, `test_conditions.py` |
| Sample rows | `_samples.py`, `for_step`, `_below`, `_recomputed`, `_join_keys` | one step, facts | rows for each schema-only table that the step reads | `test_samples.py` |
| dbt loader | `_dbt.py`, `run`, `Project` | a dbt project | input tables, declarations | `test_dbt.py` |
| dbt videos | `_dbt.py`, `model_file`, `model_steps`, `video_key`, `render_models` | a model file or name | one video for each model, a key for each video | `test_dbt.py` |
| Computed steps | `_scene.py`, `_materialize` | steps | op → a DuckDB temporary table of its rows | `test_materialize.py` |
| Re-run | `_scene.py`, `_binder`, `_run_on_samples` | a step, generated rows, computed tables | `Sample` for each input and for the output | `test_materialize.py` |
| Scene | `_scene.py`, `DebuggerScene` | all of the above | a video | render and frames |

Each test file is in the `tests/` folder of the clone.

## The data types

| Type | File | Content |
| --- | --- | --- |
| `Step` | `_tracer.py` | `op`, `inputs`, `output`, `labels`, `code`, `stack`, `args`, `kwargs`, `group_by`, `used`, `created`. Blocks key a table by its op, `table.op()`, because ibis ops are immutable values with equality. |
| `ScopeFrame` | `_scope.py` | The tables on the shelf during and after one step: `shown`, `inputs`, `output`, `after`, `replaced`, `expired`, `dead`, `dead_after`. |
| `Call` | `_scope.py` | A run of steps in one function call: `name`, `depth`, `steps`, `reads`, `hands`. |
| `Column` | `_facts.py` | `values` (a `portion` interval), `nulls` (`never`, `maybe`, `always`), `equal_to`, `subset_of`, `fields`. |
| `TableFacts` | `_facts.py` | `columns`, `keys`, `rows`, `warnings`, `ranks`, `can_empty`. |
| `Sample` | `_scene.py` | The rows that a grid shows: `label`, `columns`, `rows`, `total`, `partial`, `types`, `source`, `facts`, `changed`, `generated`. `generated` is true for rows made for a schema-only table: the grid title then shows no row count. |
| `Grid` | `_scene.py` | A table on the stage. `cells[(row, column)]`: row `-3` is the facts row, `-2` the type row, `-1` the header, `0..n` the data. |
| `Card` | `_scene.py` | A table on the shelf, in the state `input`, `output`, `idle` or `dead`. |

## Key mechanisms

- **The wrapper patches classes.** `install()` replaces the methods in `ir.Table`, `Join` and `GroupedTable` with wrappers. Thus `import ibis` in a script also gets the wrappers. A depth counter records only the outermost call, because ibis methods call other ibis methods. `Join` keeps its own copies of the `Table` methods, so it needs its own wrappers.
- **The recording is a module global.** `_tracer._steps` holds the list that receives the steps. `None` means that the recording is off. `recording(steps)` sets it inside a block. The render turns it off, because the scene calls wrapped methods such as `head`.
- **Frames give the source.** `co_positions()` and `f_lasti` give the source span of the call that runs now. `_label` finds the variable of each input by identity in `f_locals` and `f_globals`.
- **`ast` finds what the frames cannot.** `_tracer._assignment` finds the line that built a column held in a variable. `_scene._statements` finds the complete statement that holds a line, for the code bar. The library never changes or runs code through `ast`.
- **Expression substitution.** `_derive._substitute(op, {old_op: new_op})` runs a recorded expression on other inputs. The facts use placeholders `in0`, `in1`. The samples use memtables of generated rows. The re-run uses memtables of the first 6 rows, and the temporary tables of the steps before. Do not use `Node.replace` directly: it walks the whole plan below the expression at each call, so a long pipeline gets slow. `_substitute` stops the walk at the replaced nodes.
- **Each step runs once.** `_materialize` computes each step on real data into a DuckDB temporary table, on the tables of the steps before it. A count or a first-rows query then runs one step, not the pipeline from its sources. On a backend other than DuckDB, the queries run on the full plan.
- **Memtables copy their rows.** An arrow table from DuckDB keeps its DuckDB buffers, about 8 MB for each column. ibis keeps each memtable until exit. `_scene._memtable` copies the rows, so a long pipeline on a wide table does not run out of DuckDB memory.
- **Rows for each step.** `for_step` makes new rows for each step on a schema-only table, from the facts of the inputs and the arguments of the step. `_recomputed` computes the columns of earlier mutates again and drops the rows that fail earlier filters, in one flat query. `_join_keys` links the keys of each join of a chained join.
- **Facts read SQL.** `_derive._step` compiles each step to DuckDB SQL with placeholder inputs. sqlglot qualifies the SQL, so each column names its source. Then one rule for each SQL form computes the output facts: `FROM`, `JOIN`, `WHERE`, `GROUP BY`, the projection, `DISTINCT`, `LIMIT` and the set operations.
- **One video for each dbt model.** `model_steps` traces a model and the models upstream of it, groups the steps by model and derives the facts of the whole trace. It is public (`ibis_anim.model_steps`) and returns `(name, by_model, facts)`. `_trace_models` also returns the `Project`, for `render_models` and `video_key`: `Project` is not public. A step belongs to the first frame of its stack that has the name of a model, so the steps of a helper function belong to the model that called it. `render_models` renders only the steps of the model, with `render(..., facts=...)`. Thus the inputs of the model keep the facts of the upstream steps, for example `country = USA` after a filter. The facts of a slice of steps alone treat each input as a source and lose these facts. With `--deps`, `render_models` runs itself in a `ProcessPoolExecutor`, once for each model of the trace.
- **The key of a dbt video.** `video_key` hashes the file of the model and the files of the models upstream of it, the SQL models, the YAML files (`Project._yaml_files`), the `ibis_anim` source files, the quality and the manim version. The key goes to `media/keys/<model>.sha256` after a render. `render_models` skips a model when the key is the same and the key file is newer than the video. A change to any file of `ibis_anim` renders each dbt video again.
- **Copies of glyphs share their path.** manim copies each mobject at the start of each animation, and its SVG cache copies each repeated text. Each glyph of a `Text` keeps its svgelements `path_obj`, and a deep copy of it was about 90% of the cost of a copy. manim reads `path_obj` only in `__init__`. Thus `_scene.py` patches `VMobjectFromSVGPath.__deepcopy__` so that a copy keeps the path of the original. This cut a render by about 25%, with the same frames. `test_scene.py` checks it.
- **Each choreography has a fallback.** `DebuggerScene._animate` sends a step to the method of its verb. If that method raises an exception, `_flow` draws the step and stderr shows `plain animation`.

## The scene loop

`DebuggerScene.__init__` computes the plan, the names, the facts and the computed tables (`_materialize`) once. `construct` then runs these methods for each step, in one manim section per step:

1. `_header` draws the code bar, the trace, the pipeline strip and the program graph.
2. `_activate` draws the shelf cards. The inputs light up.
3. `_animate` gets the generated rows of the step from `for_step`, re-runs the step on the first 6 rows of its inputs, and calls the choreography of the verb. In a chain on a schema-only table, a caption says that the step gets its own rows.
4. `_guarantees` shows the facts that changed, and the warnings.
5. `_finish` moves the result to the shelf. If the next step reads the result as its first input, the result stays on the stage as `self.carry`.

## Extension points

| To add | Change | Recipe |
| --- | --- | --- |
| a recorded ibis method | `TABLE_METHODS` or `GROUPED_METHODS` in `_tracer.py` | [Record a new method](./recipes.md#record-a-new-method) |
| a name for the result of an operation | `_PHRASES` or `_derived_name` in `_scope.py` | [Name the result](./recipes.md#name-the-result-of-an-operation) |
| an animation | `_animate` and a new method in `_scene.py` | [Animate an operation](./recipes.md#animate-an-operation) |
| a fact or a warning | `_expr`, `_select`, `_join`, `_where` or `_set_operation` in `_derive.py` | [Add a facts rule](./recipes.md#add-a-facts-rule) |
| a hint for the sample rows | `for_step` in `_samples.py` | [Improve the sample rows](./recipes.md#improve-the-sample-rows) |
| a dbt test kind | `Project._tests` in `_dbt.py` | [Read a dbt test](./recipes.md#read-a-dbt-test) |
| a public function | `__init__.py` and its `__all__` | [Add a public function](./recipes.md#add-a-public-function) |
