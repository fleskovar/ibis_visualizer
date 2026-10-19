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
python -m ibis_anim models/marts/store_revenue.py  # a dbt-ibis model and the models upstream of it, in one video
python -m ibis_anim dbt store_revenue              # the steps of one dbt-ibis model only; faster
python -m ibis_anim dbt store_revenue --deps       # one video for that model and one for each ibis model upstream of it
```

There is also one clip per step in `media/videos/<resolution>/sections/`. To render a part of a dbt-ibis pipeline, see [Choose what to render](#choose-what-to-render).

The videos are 1080p at 30 frames per second. For faster renders, set `IBIS_ANIM_QUALITY=medium` (720p) or `low` (480p), or pass `quality=` to `ibis_anim.render`. A 1080p render takes about 2.5 times as long as a 480p render.

## Requirements

- Python 3.11 or later, `ibis-framework[duckdb]` 10 or later, and manim 0.18.1 or later.
- manim 0.19 and later need numpy 2. pyarrow before 16, for example pyarrow 14, needs numpy 1.x, but it does not declare this limit. For such a project, use Python 3.11 or 3.12, and add `numpy<2` to the requirements of the project. pip then installs manim 0.18.1, the last version that allows numpy 1.x. Without `numpy<2`, pip installs numpy 2 and manim 0.22, and pyarrow 14 fails. The tests pass with pyarrow 14.0.2, numpy 1.26.4 and manim 0.18.1.
- manim 0.18 writes the video with the `ffmpeg` and `ffprobe` programs. pip does not install them. Install FFmpeg (`winget install Gyan.FFmpeg`, `brew install ffmpeg`, `sudo apt install ffmpeg` or `conda install -c conda-forge ffmpeg`), and make sure that its `bin` folder is on the PATH. Then check in a new terminal: `ffmpeg -version` and `ffprobe -version`. The pip package `imageio-ffmpeg` is not sufficient, because it has no `ffprobe`. manim 0.19 and later need no FFmpeg program.
- No setup needs LaTeX. On Linux, if pip builds `pycairo` or `ManimPango` from source, install the cairo and pango development packages first (`libcairo2-dev`, `libpango1.0-dev`, `pkg-config`).

## How it works

1. On import, `ibis_anim` wraps the methods of `ibis.Table` and `GroupedTable` (`filter`, `mutate`, `join`, `aggregate`, `pivot_wider`, ...).
2. Each call records a step: the op, the input and output tables, the referenced columns, the Python call stack and the source text of the call. The ibis expressions are lazy, so nothing executes at this point.
3. Python control flow needs no special handling. An `if` records only the branch that ran. A loop records one step per iteration.
4. The video looks like a debugger. The video renders after the run, so it also shows what comes next.
   - Top bar: the operation, the call path (`customer_health › clean()`) and the whole current statement, with its line numbers. A long line wraps. In a statement of several lines, the line of the current call is lit. A statement of more than 6 lines shows the 6 lines around the current call.
   - Trace (left): each function call with the tables that it reads (`usa_stores(stg_stores)`), its steps under it, and a line for each table that it hands on, with the calls that read it (`→ stg_stores to usa_stores()`). Done steps have ✔, the current step has ▸, and the next steps are grey.
   - Pipeline (above the stage): the statement as a flow, for example `items → join(products, "sku") → mutate(…) → group_by(…).aggregate(…) → revenue`. The current call is lit. A chain of calls in one statement shows as one pipeline.
   - Stage (middle): the animation of the step. Each table has a row with the types of its columns above the header.
   - Program (bottom left): the program as a graph, left to right: the source tables, each function call, and the result. The current call is yellow, and the calls that ran are green. The arrows into the current call are yellow and name the tables that it reads.
   - Tables in scope (bottom right): each table is a mini table with its name (the variable that holds it), shape and first rows. The tables that the step reads light up yellow and grow. The result shrinks into a green card.
   - When the result rebinds a variable (`orders = orders.filter(...)`), or a chain goes on, the result card takes the place of the input card.
   - When the next step works on the result (`shipped = orders.filter(...)`, then `shipped.mutate(...)`), the result stays on the stage and moves into the next step. It does not go to the shelf and come back.
   - A result that no variable holds gets a name from its operation: `filtered orders`, `orders × stores`, `orders with total`, `revenue by region`. The result of a dbt-ibis model has the name of the model.
   - In a chain (`t.join(...).mutate(...).group_by(...).aggregate(...)`), the result stays on the stage and moves into the next call. Only the last result of the chain goes to the shelf. Each call of the chain runs on the rows that the call before it showed.
   - A table that no later step reads gets small and dim. Two steps after its last use, it leaves the shelf.
5. At exit, each step runs again on DuckDB, on the first 6 rows of each input only. Thus each value in the animation comes from the rows on the screen.
6. Each kind of operation has its own animation, in phases. A caption under the code explains each phase.

| Operation | Animation |
| --- | --- |
| `filter` | A label at the right of the table shows the condition (`keep rows where amount > 100`). Each row gets ✔ or ✘. The failed rows turn red and leave. The other rows slide up. |
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

A pipeline can use tables that have a schema only: `ibis.table({"order_id": "int64", ...}, name="orders")`. Then each step gets its own generated rows, about 6 for each table that it reads. Rows that are made once for the source tables do not survive the filters and joins of a long pipeline. The rows of a step come from:

- the facts of each input table: value sets, ranges, keys and nulls. The facts hold what the steps before guarantee, so the rows agree with them. After `filter(_.status == "paid")`, the next step gets rows with status `paid` only
- the arguments of the step:
  - filter, `ifelse`, `case`: each test (`==`, `>`, `isin`, `between`, `isnull`) has rows that pass it and rows that fail it. Two rows pass all tests, one row fails each test on its own, and one row fails all of them
  - join: about half of the right keys match the left keys, the other half do not. A right key that is not unique repeats, so the join fans out. In a chained join, each join of the chain gets keys that match
  - group_by, window partitions, pivot_wider: the keys repeat, so each group has several rows. A distinct gets duplicate rows, except on a table with a declared key. A union, intersect or difference gets rows that are in both tables
  - drop_null, fill_null, coalesce: the columns that can be null get nulls
- the types of the columns: `A, B, C` for strings, `1, 2, 3` for integers, spread values for floats, dates from 2024-01-01

A column that a mutate before the step computes from other columns is computed again from the generated rows: after `big=_.amount > 200`, a row with amount 250 has `big` true. A row that fails a filter before the step is removed: after `filter(_.big)`, no row has `big` false.

In a chain of steps, the result of a step stays on the stage as the input of the next step. On a table with a schema only, the next step gets its own generated rows, and a caption says so.

A step that casts a text column to another type gets no rows: the video shows the change of the types only, and the warning that a text that does not parse makes the cast fail. The grid title reads `schema only`; the title of a grid with generated rows reads `6 generated rows`, as a table with a schema only has no row count.

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

### Choose what to render

The video of a model file shows each model upstream of it too, so a model deep in the project takes a long time. The time goes into the drawing of the steps. The trace and the facts take less than a second. Render only the part of the pipeline that you want to see. The examples use `examples/dbt_shop`, where `store_revenue` reads `stg_orders`, `usa_stores` (which reads `stg_stores`) and the SQL model `stg_customers`:

| To see | Run | Result |
| --- | --- | --- |
| The whole tree in one video | `python -m ibis_anim models/marts/store_revenue.py` | `media/store_revenue.mp4`, with the 7 steps of the 4 models |
| One model | `python -m ibis_anim dbt store_revenue` | `media/store_revenue.mp4`, with the 3 steps of `store_revenue` only |
| One model, and each model upstream of it in a video of its own | `python -m ibis_anim dbt store_revenue --deps` | `media/store_revenue.mp4`, `media/stg_orders.mp4`, `media/stg_stores.mp4`, `media/usa_stores.mp4` |
| One step of a video | Open the clip of the step after a render | `media/videos/<resolution>/sections/store_revenue_0002_02_store_revenue_join.mp4` |
| Some steps of a model, or the steps of some models in one video | Python, see [Render a block of steps](#render-a-block-of-steps) | `media/<name>.mp4` |
| A large project, in small videos | The rows above, in order: see [Render a large project in chunks](#render-a-large-project-in-chunks) | one video for each model or block |

### The dbt command

```sh
python -m ibis_anim dbt MODEL [--deps] [--jobs N] [--force]
```

| Argument | Meaning |
| --- | --- |
| `MODEL` | The file of the model (`.py` or `.ibis`), or its name. For a name, the project is the nearest folder with `dbt_project.yml` at or above the current folder. |
| `--deps` | Also render a video for each ibis model upstream of `MODEL`, each in a process of its own. |
| `--jobs N` | With `--deps`, render at most `N` videos at the same time. The default is the number of CPUs. |
| `--force` | Render each video again, also when its inputs did not change. |

```sh
python -m ibis_anim dbt models/marts/store_revenue.py  # a file
python -m ibis_anim dbt store_revenue                  # a name, from a folder of the project
python -m ibis_anim dbt store_revenue --deps --jobs 2  # 4 videos, 2 at the same time
IBIS_ANIM_QUALITY=low python -m ibis_anim dbt usa_stores --force  # a quick check, rendered again
```

- The models upstream of the model run first, without a video. The facts of its inputs come from their steps, for example `country = USA` after the filter of `usa_stores`. The YAML schemas alone do not give these facts.
- The program graph of the video shows the model and the tables that it reads.
- With `--deps`, each ibis model in the trace gets its own video, in a process of its own. A SQL model or a source has no steps, so it gets no video. When the video of a model fails, the other videos continue, and the command names the models that failed.
- A step in a helper function belongs to the model that called it.
- Each process uses the memory of one render. For a project with many large models, use a lower `--jobs`.
- A video renders again only when one of its inputs changes. The key of a video is `media/keys/<model>.sha256`. It is a hash of:
  - the files of the model and of the models upstream of it,
  - the SQL models and the YAML files of the project,
  - the code of ibis_anim, the quality and the manim version.

  A change in a model downstream of the model does not change its key. When a video did not change, the command writes `media/<model>.mp4 is up to date`. To render the videos again, add `--force`. When another render writes the video later, for example `python -m ibis_anim FILE`, the next `dbt` command renders the video again.
- If `IBIS_ANIM=0`, the command renders nothing and writes no key.

### Render a block of steps

To render some steps of a model, or the steps of some models in one video, call `ibis_anim.model_steps(path)`. It traces the model and the models upstream of it, with no video, and returns `(name, by_model, facts)`: the name of the model, the steps of each ibis model, and the facts of each table of the trace. Give some of the steps to `ibis_anim.render`, with the facts:

```python
import ibis_anim

name, by_model, facts = ibis_anim.model_steps("models/marts/store_revenue.py")  # a trace, no video
# by_model: model name → its steps, in the order that they ran
# {'stg_orders': [filter, mutate], 'stg_stores': [mutate], 'usa_stores': [filter], 'store_revenue': [join, join, aggregate]}

steps = by_model["store_revenue"]
ibis_anim.render(steps[:2], "store_revenue_joins", facts=facts)                       # the 2 joins only
ibis_anim.render(by_model["stg_stores"] + by_model["usa_stores"], "stores", facts=facts)  # 2 models in one video
```

- Always give `facts=facts`. Without it, the facts come from the given steps only. Then each input of the first step is a source with no facts, and the generated rows ignore the filters before it.
- The steps of a video count from 1. The trace and the program graph show the given steps only.
- `render` renders also when `IBIS_ANIM=0`, and it writes no key.

### Render a large project in chunks

A render takes about 20 seconds for each step at `low`, and about 2.5 times as long at `high`. Thus one video of a project with 100 steps takes more than half an hour at `low`. Do not render a large project in one video. Render it in chunks: one video for each model, and one video for each block of steps of a large model.

The script [`hcm-bundle/skills/ibis-anim/chunks.py`](hcm-bundle/skills/ibis-anim/chunks.py) lists the models and their steps, and renders a model in blocks of steps. It uses `ibis_anim.model_steps` and `ibis_anim.render`, as in [Render a block of steps](#render-a-block-of-steps). Run each command from a folder of the dbt project:

1. List the models of the trace and their steps. This renders nothing and takes a few seconds:

   ```sh
   IBIS_ANIM=0 python chunks.py models/marts/store_revenue.py
   ```

   ```
   stg_orders: 2 steps: filter, mutate
   stg_stores: 1 step: mutate
   usa_stores: 1 step: filter
   store_revenue: 3 steps: join, join, group_by → aggregate
   ```

   The models are in the order that they ran. SQL models, seeds and sources have no steps, so they are not in the list. Multiply the steps of a model by 20 seconds to get its render time at `low`.

2. Render one model at `low` to check it: `IBIS_ANIM_QUALITY=low python -m ibis_anim dbt store_revenue`.

3. Render each model of the tree at `low`, each model in its own video and its own process:

   ```sh
   IBIS_ANIM_QUALITY=low python -m ibis_anim dbt store_revenue --deps --jobs 4
   ```

   With enough jobs, the time is about the time of the largest model. If a model fails, the other models continue. Correct the model and run the same command again: only the models that failed or changed render again.

4. If a model has more than about 10 steps, render it in blocks of steps:

   ```sh
   IBIS_ANIM_QUALITY=low python chunks.py models/marts/store_revenue.py --size 5
   IBIS_ANIM_QUALITY=low python chunks.py models/marts/store_revenue.py --size 5 --model stg_orders   # a model upstream of it
   ```

   The videos are `media/store_revenue_part1.mp4`, `media/store_revenue_part2.mp4` and so on. Each part has the facts of the whole trace. The step counter and the clip names of each part start at 1.

5. Render the final videos at `high`: `python -m ibis_anim dbt store_revenue --deps`. Render the final blocks with `chunks.py` and no `IBIS_ANIM_QUALITY`.

Rules for chunks:

- The key of a video includes the quality. Thus the render at `high` renders each video again, and it replaces the `low` video in `media/<model>.mp4`. The key file keeps the last key only, so the next render at `low` renders each video again too.
- `chunks.py` writes no key. Each run renders each part again.
- Each process of `--deps` uses the memory of one render. If the computer runs out of memory, use a lower `--jobs`.

### Render time

The times are for `examples/dbt_shop` at `IBIS_ANIM_QUALITY=low`, on 12 CPUs:

| Run | Time |
| --- | --- |
| `python -m ibis_anim models/marts/store_revenue.py`, the whole tree | about 130 seconds |
| `python -m ibis_anim dbt store_revenue` | about 65 seconds |
| `python -m ibis_anim dbt store_revenue --deps`, 4 videos | about 80 seconds |
| The same command, when no input changed | about 9 seconds |
| `--deps` after a change to `store_revenue` only | about 70 seconds: only `store_revenue` renders |

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
python -m ibis_anim dbt examples/dbt_shop/models/marts/store_revenue.py --deps  # one video for each model
IBIS_ANIM=0 python my_script.py      # run without the video
python tests/test_tracer.py          # checks of the tracer
python tests/test_scope.py           # checks of the tables panel plan
python tests/test_facts.py           # checks of the facts and warnings
python tests/test_conditions.py      # checks of the conditions of filters and ifelse
python tests/test_samples.py         # checks of the generated rows
python tests/test_dbt.py             # checks of dbt-ibis models
python tests/test_scene.py           # checks of the drawing
```

To render one function only, call `ibis_anim.visualize(func, *args)`. To get the steps without a video, call `ibis_anim.trace(func, *args)`.

## Limits of the MVP

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The result values come from these rows only.
- When an animation cannot handle a call, the step uses the plain column animation and prints the reason on stderr.
- Recording is single-threaded.
- The window animation draws row frames only. It does not draw range frames.
