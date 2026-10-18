# ibis-anim

A visual debugger for [ibis](https://ibis-project.org) code. It renders each table operation as a short [manim](https://www.manim.community) animation.

`ibis_anim` is a drop-in replacement for the `ibis` module. Change the import and run the script:

```python
import ibis_anim as ibis          # was: import ibis
from ibis_anim import _           # was: from ibis import _
import ibis_anim.selectors as s   # was: import ibis.selectors as s
```

The script runs as before. At exit, the video goes to `media/<script>.mp4`.

To keep the code unchanged (`import ibis`), run it through `ibis_anim` instead:

```sh
python -m ibis_anim my_script.py [ARGS...]         # the whole script; the video is media/my_script.mp4
python -m ibis_anim my_script.py:pipeline          # one function, called without arguments; media/pipeline.mp4
python -m ibis_anim models/marts/store_revenue.py  # a dbt-ibis model and the models upstream of it
``` There is also one clip per step in `media/videos/<resolution>/sections/`.

The videos are 1080p at 30 frames per second. For faster renders, set `IBIS_ANIM_QUALITY=medium` (720p) or `low` (480p), or pass `quality=` to `ibis_anim.render`. A 1080p render takes about 2.5 times as long as a 480p render.

## How it works

1. On import, `ibis_anim` wraps the methods of `ibis.Table` and `GroupedTable` (`filter`, `mutate`, `join`, `aggregate`, `pivot_wider`, ...).
2. Each call records a step: the op, the input and output tables, the referenced columns, the Python call stack and the source text of the call. The ibis expressions are lazy, so nothing executes at this point.
3. Python control flow needs no special handling. An `if` records only the branch that ran. A loop records one step per iteration.
4. The video looks like a debugger. The video renders after the run, so it also shows what comes next.
   - Top bar: the operation, the call path (`customer_health › clean()`) and the current statement with its line.
   - Trace (left): each function call, with its steps under it. Done steps have ✔, the current step has ▸, and the next steps are grey.
   - Pipeline (above the stage): the statement as a flow, for example `items → join(products, "sku") → mutate(…) → group_by(…).aggregate(…) → revenue`. The current call is lit. A chain of calls in one statement shows as one pipeline.
   - Stage (middle): the animation of the step. Each table has a row with the types of its columns above the header.
   - Tables in scope (bottom): each table is a mini table with its name (the variable that holds it), shape and first rows. The tables that the step reads light up yellow and grow. The result shrinks into a green card.
   - When the result rebinds a variable (`orders = orders.filter(...)`), or a chain goes on, the result card takes the place of the input card.
   - In a chain (`t.join(...).mutate(...).group_by(...).aggregate(...)`), the result stays on the stage and moves into the next call. Only the last result of the chain goes to the shelf. Each call of the chain runs on the rows that the call before it showed.
   - A table that no later step reads gets small and dim. Two steps after its last use, it leaves the shelf.
5. At exit, each step runs again on DuckDB, on the first 6 rows of each input only. Thus each value in the animation comes from the rows on the screen.
6. Each kind of operation has its own animation, in phases. A caption under the code explains each phase.

| Operation | Animation |
| --- | --- |
| `filter` | Each row gets ✔ or ✘. The failed rows turn red and leave. The other rows slide up. |
| `filter` with several tests (`ibis.and_`, `ibis.or_`, `&`, `\|`) | Each test gets a column of ✔/✘, with the columns that it reads highlighted. Then, per row, the marks merge into the result column (`1 AND 2`, `1 OR 2`, `1 AND (2 OR 3)`), and the failed rows leave. A null test shows `–`: it does not keep the row. |
| `ibis.ifelse` in `mutate` or `select` | A legend shows the two branches (`✔ → 'large'`, `✘ → 'small'`). Per row, the cells of the test flash, the row gets ✔/✘, and the value of the chosen branch moves into the new cell: the row's own cell for a column, the legend for a constant. |
| `ibis.coalesce` in a new column | First, the columns of the arguments move into the order of the arguments, at the left of the table. A number (1, 2, …) marks each argument, on its column or on a chip for a constant. Per row, a pointer starts at argument 1 and reads from left to right. A null turns the pointer red (`null → next`) and sends it to the next argument. The first value that is not null turns the pointer green and moves into the new cell. When all the values are null, the result is null. At the end, the columns go back to their place in the table. |
| `distinct` | The caption names the compared columns (all of them, or the `on=` columns). The current row is outlined in yellow, and a scanner moves down the rows below it. The rows with the same values turn red and leave, and the other rows slide up. Then the next row is the current row. |
| `drop_null`, semi and anti `join` | The same as `filter`, with the test of the operation. |
| `limit`, `head`, `sample` | A cut line or a count, then the removed rows leave. |
| `order_by` | The sort column is yellow, an arrow shows the direction, and the rows move along arcs to their new positions. |
| `mutate`, `select`, `cast` | A caption shows the formula of each new column. For each row, the source cells move into the new cell, with a short delay between rows. |
| `select`, `drop`, `rename`, `relocate` | Dropped columns turn red. Kept columns move to their new position. Renamed headers change their text. |
| window functions | The rows take the colour of their partition and are sorted. Then a frame moves down the rows and shows the rows of each value. |
| `group_by` → `aggregate` | The rows take the colour of their group and come together. Then each group in turn collapses into one result row. |
| `join` | The join columns move to the inner edges and the tables come together. The rows line up on equal keys, and the key columns overlap and merge. |
| `pivot_wider` | Distinct names move into column headers. The id columns become one row per value. The empty cells light up, then each value moves to its cell. |
| `pivot_longer` | For each row, the pivoted cells rotate into a vertical column of the result. |
| `union` | The rows of the second table go under the rows of the first. With `distinct=True`, the repeated rows leave. |
| `intersect`, `difference` | The caption says that the whole row is compared, and names all the columns. For each row of the first table, a solid line joins it to the equal row of the second table (✔). When the nearest row has only some equal values, a dashed red line points to it, the cells that differ turn red, and a label (`≠ score`) names them (✘). Then the rows that do not stay leave. |
| `fill_null` | The null cells light up red and take the replacement value. |
| `unnest` | Each array cell splits into its elements, and each element moves to its own row. |
| other ops | Each result column comes from the input column with the same name. |

When a step changes the type of a column (`cast`, `try_cast`, or a `mutate` with `.cast(...)`), the type cell changes after the values convert, for example `float64 → int64`.

Columns that the arguments read are yellow. New columns are green.

A column that you build outside a table call is a step of its own. For example:

```python
label = ibis.coalesce(players.nick, players.player)
points = (players.score * 10).cast("int64")
players = players.mutate(label=label, points=points)
```

This gives three steps. The first two are `column` steps, at the lines of the assignments: the source cells merge into a new one-column table, `label` or `points`, and that table goes to the shelf as "a column of players". At the `mutate`, the cards of `label` and `points` light up, their tables come onto the stage, and each cell moves into the new column, to its row. The column step is recorded when a table call reads the variable. An expression that you write in the call itself (`mutate(x=t.a + 1)`) is part of that step.

## Facts: what each step guarantees

`ibis_anim` does an abstract interpretation of the pipeline. You state what is known about the source tables. Then each step derives what is known about its result. No data is necessary: the source tables can be `ibis.table(schema, name=...)`.

```python
ibis.declare(customers, key="customer_id", values={"plan": {"pro", "basic", "trial"}})
ibis.declare(orders, key="order_id", values={"status": {"paid", "refunded"}, "amount": (0, None)},
             references={"customer_id": customers.customer_id})
print(ibis.report(pipeline))  # the facts and warnings of each step, as text
```

The facts never come from the data. Declare only constraints that hold for any data: keys, foreign keys, an enum type, a `CHECK` constraint, a business rule. Do not declare the values that you see in a sample: that is data profiling, and the facts would then be wrong for other data. The examples declare only keys and foreign keys. Without a declaration, `plan != "trial"` gives `≠ trial` (any value except `trial`), not the values that are left in the sample.

For each column, the facts are:

- the set of possible values: a finite set `{paid}`, a range `≥ 0`, or a complement `≠ trial`
- the nulls: never, maybe or always. `not null` says that no row has a null in the column. It does not say that there is a row: an empty column has no nulls too. For example, after `filter(_.revenue > 20)`, `revenue` is `not null`, because a null does not pass `> 20`.
- uniqueness, and the keys of the table
- for the table: the row count compared with the input (`= input`, `≤ input`, `≥ input`), and whether the step can return no row even when its input has rows (`can be empty`). A filter, an inner join, `intersect`, `difference`, `sample`, `unnest` and `pivot_longer` can. A left join, `group_by`, `distinct`, `mutate` and `order_by` cannot. An aggregate without `group_by` always gives one row.
- the lineage: `= customers.customer_id` (exactly the same set of values) or `⊆ customers.customer_id` (a subset). The lineage names the most upstream table that still holds: a source table, or the earliest named variable. The unnamed results of a chain are not used. Thus the label changes only when the relation changes, for example from `=` to `⊆` after an inner join.

Each step compiles to SQL with its inputs as placeholders. sqlglot qualifies the SQL. Then one sound rule for each SQL form (WHERE, JOIN, GROUP BY, DISTINCT, projection, set operation, window, LIMIT) computes the facts of the result. For example, a filter `amount > 100` gives `amount > 100 · not null`; a left join keeps the set of the left key (`= active.customer_id`) and keeps its keys when the right key is unique; `group_by` makes its keys unique; `fill_null` removes the nulls. A form that the rules do not know gives no fact. Thus a fact is never wrong, but it can be missing.

The analysis also finds possible mistakes:

- a filter that keeps no row, for example `status == "payed"` when `status` is in `{paid, refunded}`
- a join that can repeat rows, because the right key is not known to be unique (fan-out)
- join keys with no value in common
- a division by a column that can be 0
- a cast that drops the fraction of a value

In the video, each table has a facts row above its type row. `any` means that no value set is known. After each step, a caption lists what the step guarantees, and the changed facts light up. Warnings show in red, and the trace marks the steps that will warn with ⚠ before they run. At exit, the warnings also print to the terminal.

## Pipelines without data

A pipeline can use tables that have a schema only: `ibis.table({"order_id": "int64", ...}, name="orders")`. Then ibis_anim generates a few rows for each such table when it renders the video. The rows come from the types of the columns and from the code:

- each type has simple values: `A, B, C` for strings, `1, 2, 3` for integers, spread values for floats, dates from 2024-01-01
- a declared key has distinct values; other columns repeat values, with different periods, so that their values combine in different ways
- two columns that a join compares only partly match: one gets `A, B, C`, the other `A, D, C`. A declared foreign key (`references=`) takes its values from the column that it references
- a column that the code compares with a literal (`status == "paid"`, `amount > 100`) gets the literal and values on both sides of the test, so that each test has rows that pass and rows that fail

The generated rows illustrate the steps only. The facts do not come from them. See `examples/schema_only.py`.

## dbt-ibis models

ibis_anim runs a [dbt-ibis](https://github.com/binste/dbt-ibis) model together with every model upstream of it. Give the file of the model:

```sh
python -m ibis_anim models/marts/store_revenue.py  # or .ibis; the video is media/store_revenue.mp4
```

From Python, pass the `model` function, which has `@depends_on(...)`, without arguments: `ibis_anim.visualize(ibis_anim.load_dbt_model("models/marts/store_revenue.py"))`.

- The project is the nearest folder above the model file that has `dbt_project.yml`. Its model folders hold the models: `name.py` or `name.ibis` for ibis models, `name.sql` for SQL models.
- Each `ref("name")` to an ibis model runs that model first, with its own references. The steps of each model show under its own name in the trace (`stg_stores()`, not `model()`).
- A `source("src", "table")`, or a `ref` to a SQL model, is a table with a schema only. Its column types come from `data_type` in the YAML files of the project, as dbt-ibis requires. The video uses generated rows for it.
- The dbt tests of the columns in the YAML (`unique`, `not_null`, `accepted_values`, and the constraints `primary_key` and `not_null`) are facts about the table. dbt checks them at each build. This also applies to ibis models: a test on `stg_stores.store_id` keeps the column unique after a cast.
- Not yet: `relationships` tests, and SQL models with `SELECT *` and no columns in the YAML.

See `examples/dbt_shop/`. Install the extra with `pip install ibis-anim[dbt]`.

## Use

```sh
python examples/orders_pipeline.py   # filter, mutate, join, aggregate, one branch
python examples/pivots.py            # pivot_wider, mutate, pivot_longer
python examples/rolling.py           # rolling sum, cumulative sum, order_by, limit
python examples/table_ops.py         # union, distinct, fill_null, cast, new columns, select, rename, unnest, intersect
python examples/customer_health.py   # a realistic job: 5 tables, chains, a loop, a branch, 2 left joins, a rank (22 steps)
python examples/conditions.py        # ibis.and_ and ibis.or_ in filters, ibis.ifelse in new columns
python examples/schema_only.py       # schemas only, no data: the video uses generated rows
python -m ibis_anim examples/dbt_shop/models/marts/store_revenue.py  # a dbt-ibis model and the models upstream of it
IBIS_ANIM=0 python my_script.py      # run without the video
python tests/test_tracer.py          # checks of the tracer
python tests/test_scope.py           # checks of the tables panel plan
python tests/test_facts.py           # checks of the facts and warnings
python tests/test_conditions.py      # checks of the conditions of filters and ifelse
python tests/test_samples.py         # checks of the generated rows
python tests/test_dbt.py             # checks of dbt-ibis models
```

To render one function only, call `ibis_anim.visualize(func, *args)`. To get the steps without a video, call `ibis_anim.trace(func, *args)`.

## Limits of the MVP

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The result values come from these rows only.
- When an animation cannot handle a call, the step uses the plain column animation and prints the reason on stderr.
- Recording is single-threaded.
- The window animation draws row frames only. It does not draw range frames.
