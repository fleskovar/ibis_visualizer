---
description: Render a debugger video of ibis code with ibis-anim, from Python or with the command line `python -m ibis_anim FILE[:FUNCTION]`, render one dbt-ibis model, its upstream models or a block of its steps with `python -m ibis_anim dbt MODEL [--deps]` and `chunks.py`, render a large dbt-ibis project in chunks, read the facts and warnings that it computes, and look at the frames of a render. Covers the caveats of dbt-ibis models, and the setup for a project that must keep numpy 1.x (pyarrow 14, Python 3.11 or 3.12, manim 0.18.1, FFmpeg). Use when the user asks to visualize, animate, explain or debug an ibis pipeline, one function of a file, a dbt-ibis model or a script that imports ibis_anim, to render a part of a dbt-ibis pipeline, or to install ibis-anim next to an old pyarrow or numpy.
flavors: [use, develop]
---

# Use ibis-anim

`ibis_anim` records each ibis table call of a program while the program runs. At exit, it renders a manim video with one section for each call. The video shows the code, the call stack, the tables in scope, an animation of the operation on the first rows of its inputs, and the facts that the step guarantees.

`ibis_anim` is a drop-in replacement for `ibis`. It re-exports every name and submodule of `ibis`, and it wraps the methods of the ibis table classes in place. The program gives the same results with and without it.

## Install

Install the package `ibis-anim` in the Python environment of the project. For dbt-ibis models, install the extra `ibis-anim[dbt]`. The package needs Python 3.11 or later, `ibis-framework[duckdb]` 10 or later, and `manim` 0.18.1 or later. If `import manim` fails, follow the manim installation guide for the platform.

A project with numpy 1.x, for example for pyarrow 14, needs a different setup: Python 3.11 or 3.12, `numpy<2` in its requirements, manim 0.18.1, and the `ffmpeg` and `ffprobe` programs on the PATH. Follow [the numpy 1.x setup](./numpy1.md). It gives each step, the checks and the errors of a wrong setup.

External programs that pip does not install:

| Program | Current setup (manim 0.19 or later) | numpy 1.x setup (manim 0.18.1) |
| --- | --- | --- |
| `ffmpeg` | not needed: the PyAV wheels contain FFmpeg | needed on the PATH |
| `ffprobe` | not needed | needed on the PATH |
| cairo and pango libraries | only on Linux, if pip builds `pycairo` or `ManimPango` from source | the same |
| LaTeX | never | never |

To install FFmpeg, put it on the PATH and check the PATH, see [External programs](./numpy1.md#external-programs).

## Render a video

Choose one of three ways. They give the same video.

1. **Change the import.** Replace the imports of the script and run it as before:

   ```python
   import ibis_anim as ibis          # was: import ibis
   from ibis_anim import _           # was: from ibis import _
   import ibis_anim.selectors as s   # was: import ibis.selectors as s
   ```

   At exit, the video goes to `media/<script name>.mp4`.

2. **Use the command line.** The code does not change. It can keep `import ibis`. See [The command line](#the-command-line).

3. **Call the API.** Use this way for a function that needs arguments:

   ```python
   import ibis_anim
   path = ibis_anim.visualize(pipeline, orders, customers)   # returns the path of the .mp4 file
   ```

## The command line

```sh
python -m ibis_anim FILE[:FUNCTION] [ARGS...]
python -m ibis_anim dbt MODEL [--deps] [--jobs N] [--force]
```

The command has four forms:

| Form | What runs | What the video shows | Video file |
| --- | --- | --- | --- |
| `python -m ibis_anim FILE [ARGS...]` | The file runs as a script, the same as `python FILE ARGS`. | Each table call of the whole run. | `media/<file name without extension>.mp4` |
| `python -m ibis_anim FILE:FUNCTION [ARGS...]` | The module-level code of the file runs. Then the command calls `FUNCTION` without arguments. | Only the table calls in `FUNCTION` and in the functions that it calls. | `media/<FUNCTION>.mp4` |
| `python -m ibis_anim MODEL_FILE` | A dbt-ibis model and the ibis models upstream of it. See [dbt-ibis models](#dbt-ibis-models). | Each table call of each model. | `media/<model name>.mp4` |
| `python -m ibis_anim dbt MODEL` | The same, but only the steps of `MODEL` get a video. See [The dbt command](#the-dbt-command). | The table calls of `MODEL` only. | `media/<model name>.mp4`, and with `--deps` one video for each upstream ibis model |

Examples:

```sh
IBIS_ANIM_QUALITY=low python -m ibis_anim pipelines/orders.py:clean_orders   # a quick check of one function
python -m ibis_anim jobs/report.py --date 2024-01-01                          # the whole script, with its arguments
```

These rules apply to the form `FILE:FUNCTION`:

- The module-level code of the file runs first, with the recording off. Its table calls are not in the video. The tables that it defines are inputs of the function.
- The block `if __name__ == "__main__":` does not run, because the file does not run as `__main__`.
- The command calls the function without arguments. Each parameter of the function must have a default value. Otherwise the command fails with `TypeError: ... missing 1 required positional argument`. For a function that needs arguments, add a function without parameters that calls it. Or call `ibis_anim.visualize(func, *args)` from Python.
- `FUNCTION` must be a name at module level of the file. If the file has no such name, the command prints `ibis_anim: FILE has no function FUNCTION` and exits with status 1.
- `ARGS` go to `sys.argv` of the file. The function does not receive them as arguments.

These rules apply to all forms:

- The command splits the argument at the last `:`. If the text after it is not a Python identifier, the whole argument is the file. Thus a Windows path such as `C:\jobs\report.py` works, and `C:\jobs\report.py:main` also works.
- The command adds the folder of the file to `sys.path`. The file can import the files beside it, the same as with `python FILE`.
- The command renders at exit, the same as an import of `ibis_anim`. If `IBIS_ANIM` is `0`, the command renders nothing. To check a render quickly, set `IBIS_ANIM_QUALITY=low` instead.

## Settings

| Variable | Values | Default | Effect |
| --- | --- | --- | --- |
| `IBIS_ANIM` | `0`, `1` | `1` | `0` turns off the render at exit. Use it for tests and quick runs. |
| `IBIS_ANIM_QUALITY` | `low` (480p, 15 fps), `medium` (720p, 30 fps), `high` (1080p, 30 fps) | `high` | The resolution of the video. A 1080p render takes about 2.5 times as long as a 480p render. |

`render(steps, name, output_dir="media", quality=None, facts=None)` takes the same values as arguments. Most of the time goes to manim, about 20 seconds for each step at `low`: the example `customer_health.py`, with 22 steps, takes about 8 minutes at `low`. Use `low` to check a change, and `high` for the final video.

## The API

| Name | Use |
| --- | --- |
| `trace(func, *args, **kwargs)` | Call `func` and return `(result, steps)`. No video. |
| `visualize(func, *args, **kwargs)` | Trace `func` and render its video. Returns the path. |
| `render(steps, name, output_dir="media", quality=None, facts=None)` | Render a list of steps. Returns the path. `facts` are the facts of a larger trace. Give them when the steps are a part of a trace, see [Render a block of steps](#render-a-block-of-steps). |
| `report(func, *args, **kwargs)` | Trace `func` and return the facts and warnings of each step as text. No video. |
| `declare(table, key=None, values=None, not_null=(), references=None, unique=())` | State constraints of a source table. See [Facts](#facts-and-warnings). |
| `recording(steps)` | A context manager. `recording(None)` turns off the recording inside the block. |
| `load_dbt_model(path)` | The `model` function of a dbt-ibis file. |
| `model_steps(path)` | Trace a dbt-ibis model and the models upstream of it. No video. Returns `(name, by_model, facts)`: the name of the model, a dict of model name → its steps in the order that the models ran, and the facts of each table of the trace. See [Render a block of steps](#render-a-block-of-steps). |
| `Step` | The record of one table call: `op`, `inputs`, `output`, `labels`, `code`, `stack`, `used`, `created`. |

A `dbt-ibis` model function given to `trace` or `visualize` without arguments gets its inputs from its dbt project.

## Facts and warnings

For each step, ibis-anim computes what the result guarantees for any data: value sets, nulls, keys, lineage and the row count compared with the input. The facts never come from the rows. Without declarations, the facts come from the schema and the code only.

Declare the constraints that hold for any data, for example an enum, a `CHECK` constraint or a business rule:

```python
ibis.declare(orders, key="order_id",
             values={"status": {"paid", "refunded"}, "amount": (0, None)},   # a set, or a (low, high) range; None is unbounded
             not_null=["customer_id"],
             references={"customer_id": customers.customer_id})
```

Do not declare values that you read from a sample of the data. A wrong declaration gives wrong facts.

To read the facts without a render, print `ibis_anim.report(pipeline, *args)`:

```
 1. typo() › filter(orders) → filtered orders   rows ≤ input · can be empty
      keys: (order_id)
      status: ∅ (no value) · not null · ⊆ orders.status
      ⚠ status has no possible value after `status = 'payed'`: the filter keeps no row
```

The analysis warns about these possible mistakes:

- a filter that keeps no row
- a join that can repeat rows, because the right key is not known to be unique (fan-out)
- join keys with no value in common
- a division by a column that can be 0
- a cast that drops the fraction of a value

At exit, the render also prints each warning to the terminal, with the prefix `ibis_anim: ⚠`.

## Pipelines without data

A table with a schema only, `ibis.table({"order_id": "int64", ...}, name="orders")`, has no rows. For such a table, each step gets its own generated rows, about 6 for each table that it reads:

- The rows agree with the facts of the steps before. After `filter(_.status == "paid")`, the next step gets rows with status `paid` only.
- Each test of a filter has rows that pass it and rows that fail it. A filter `amount > 100` gets values on both sides of 100.
- About half of the keys of a join match. Each join of a chained join gets keys that match.
- A column that an earlier mutate computes agrees with the other columns of its row. A row that fails an earlier filter is removed.
- In a chain, a caption says that the step gets its own rows, not the rows of the step before.
- A step that casts text to another type gets no rows. The video shows the change of the types only.

The generated rows illustrate the steps only. The facts do not use them. A grid of generated rows shows `6 generated rows`, not a row count.

## dbt-ibis models

Install the extra `ibis-anim[dbt]`, which installs `dbt-ibis`. Then give the file of the model to the command. The file can be a `.py` or a `.ibis` file. Both commands below render the same video, `media/store_revenue.mp4`:

```sh
python -m ibis_anim models/marts/store_revenue.py
python -m ibis_anim models/marts/store_revenue.py:model
```

From Python, call `ibis_anim.visualize(ibis_anim.load_dbt_model("models/marts/store_revenue.py"))`. Give no arguments: the model gets its inputs from its project.

### How the loader finds the inputs

- The project is the nearest folder above the model file that has `dbt_project.yml`. Without such a folder, the project is the folder of the model file.
- The loader reads the folders in `model-paths` and `seed-paths` of `dbt_project.yml`. The defaults are `models` and `seeds`. It skips the folders named `__ibis_sql`, which hold the SQL that dbt-ibis generates.
- A `ref("name")` to an ibis model (`name.py` or `name.ibis`) runs that model first, with its own references, under the same recording. The video shows the steps of each model under the name of the model, for example `stg_stores()`, not `model()`.
- A `source("src", "table")`, or a `ref` to a SQL model or a seed, gives a table with a schema only. The video shows generated rows for it. See [Pipelines without data](#pipelines-without-data).

### Caveats

**No dbt run and no warehouse.** ibis-anim does not run dbt, and it does not connect to the warehouse. It does not read `profiles.yml`. It does not read the CSV files of seeds. The rows of sources, SQL models and seeds in the video are generated. They are not the data of the warehouse.

**The model file.** The file must define a function `model` (or `test`) with `@depends_on(...)`. If the file has no such function, the command runs the file as a plain script.

**References.** The loader finds a `ref` by the file name of the model only. It ignores the `alias` config and a package name. If two files in the project have the same name, the loader uses the first file that it finds.

**Each upstream model runs.** Each ibis model upstream of the model runs again in the process. With `python -m ibis_anim MODEL_FILE`, each step of each model is in the video, so a large project gives a long video. To show the steps of one model only, use [the dbt command](#the-dbt-command).

**Column types.** A source, a SQL model and a seed need a `data_type` for each column in a YAML file of the project, as dbt-ibis requires. If the loader finds no columns, it stops with `no columns for the source raw.orders: declare its columns with a data_type in a YAML file of the project`.

- Use type names that DuckDB knows: `bigint`, `double`, `varchar`, `boolean`, `date`, `timestamp`, `decimal(10, 2)`, `varchar[]`. Some warehouse names also work: `NUMBER(38,0)` gives `decimal(38, 0)`, `VARIANT` gives `json`, `timestamp_ntz` gives `timestamp`.
- DuckDB reads `int` as `int32` and `float` as `float32`.
- A type that DuckDB does not know gives an `unknown` type. Examples are the Redshift type `super` and the ibis name `float64`. A column of an unknown type has no numeric or text methods, so the model fails, for example with `AttributeError: 'UnknownColumn' object has no attribute 'sum'`.
- If a SQL model has no columns in YAML, the loader reads the names of the columns from its `SELECT`. A column gets the type of its `CAST`, else `string`. Thus `select customer_id from ...` gives a `string` column. A model with `SELECT *` gives no columns. Declare the types in YAML for each SQL model that an ibis model reads.
- An ibis model needs no types, because it runs. Its YAML entry can still give tests.

**Tests become facts.** The loader turns some tests and constraints of the YAML columns into `declare` facts about the table:

- It reads the tests `unique`, `not_null` and `accepted_values` (with `values`), from the keys `tests` and `data_tests`. It reads the constraints `primary_key`, `unique` and `not_null`.
- It ignores the test `relationships`, the tests of a whole model, the tests of packages such as `dbt_utils`, and custom generic tests.
- It ignores the `config` of a test. A test with a `where` filter, or with `severity: warn`, becomes a fact about all rows. If such a test does not hold for all rows, the facts of that column are wrong. Check the facts of that column in the video.
- The tests of an ibis model apply to its output. For example, a `unique` test on `stg_stores.store_id` keeps the column unique after a cast.

### Choose what to render

The time of a render goes into the drawing of the steps. The trace and the facts take less than a second. Thus render only the part of the pipeline that you want to see. In the examples, `store_revenue` reads `stg_orders`, `usa_stores` (which reads `stg_stores`) and the SQL model `stg_customers`:

| To see | Run | Result |
| --- | --- | --- |
| The whole tree in one video | `python -m ibis_anim models/marts/store_revenue.py` | `media/store_revenue.mp4`, with the 7 steps of the 4 models |
| One model | `python -m ibis_anim dbt store_revenue` | `media/store_revenue.mp4`, with the 3 steps of `store_revenue` only |
| One model, and each upstream model in a video of its own | `python -m ibis_anim dbt store_revenue --deps` | `store_revenue.mp4`, `stg_orders.mp4`, `stg_stores.mp4` and `usa_stores.mp4` in `media/` |
| One step of a video | Open the clip of the step after a render | `media/videos/<resolution>/sections/store_revenue_0002_02_store_revenue_join.mp4` |
| Some steps of a model, or the steps of some models in one video | Python | See [Render a block of steps](#render-a-block-of-steps) |
| A large project, in small videos | Each of the above, in order | See [Render a large project in chunks](#render-a-large-project-in-chunks) |

On `examples/dbt_shop` at `low`, the whole tree takes about 130 seconds, one model about 65 seconds, and `--deps` about 80 seconds for 4 videos.

### The dbt command

```sh
python -m ibis_anim dbt MODEL [--deps] [--jobs N] [--force]
```

| Argument | Meaning |
| --- | --- |
| `MODEL` | The file of the model (`.py` or `.ibis`), or its name. For a name, the project is the nearest folder with `dbt_project.yml` at or above the current folder. If the command finds no such model, it prints `ibis_anim: no ibis model NAME in ROOT` and exits with status 1. |
| `--deps` | Also render a video for each ibis model upstream of `MODEL`, each in a process of its own. A SQL model, a seed and a source have no steps, so they get no video. |
| `--jobs N` | With `--deps`, render at most `N` videos at the same time. The default is the number of CPUs. Each process uses the memory of one render. For a project with many large models, use a lower `N`. |
| `--force` | Render each video again, also when its inputs did not change. |

These rules apply to the dbt command:

- The models upstream of `MODEL` run first, without a video. The facts of the inputs of `MODEL` come from their steps, for example `country = USA` after the filter of `usa_stores`. The YAML schemas alone do not give these facts.
- The trace and the program graph of the video show `MODEL` and the tables that it reads.
- A step in a helper function belongs to the model that called the function.
- With `--deps`, when the video of a model fails, the other videos continue. At the end, the command prints `ibis_anim: no video for MODEL, ...` and exits with status 1.
- A model with no table calls gets no video. The command prints `ibis_anim: MODEL has no ibis steps`.
- If `IBIS_ANIM` is `0`, the command renders nothing.

The command renders a video again only when one of its inputs changed. After a render, it writes the key of the video to `media/keys/<model>.sha256`. The key is a hash of:

- the file of the model and the files of the models upstream of it,
- the SQL models and the YAML files of the project,
- the code of `ibis_anim`, the value of `IBIS_ANIM_QUALITY` and the manim version.

A change in a model downstream of the model does not change its key. When the key did not change, the command prints `ibis_anim: media/<model>.mp4 is up to date` and does not render. A later render of `media/<model>.mp4` by a different command, for example `python -m ibis_anim MODEL_FILE`, makes the video newer than its key: the next `dbt` command renders it again. With `--deps`, each process loads and traces its model to compute the key, about 4 seconds for each model, in parallel.

### Render a block of steps

To render some steps of a model, or the steps of some models in one video, call `model_steps(path)`. Then give some of the steps to `render`, with the facts of the whole trace:

```python
import ibis_anim

name, by_model, facts = ibis_anim.model_steps("models/marts/store_revenue.py")  # a trace, no video
# by_model: model name → its steps, in the order that they ran
# {'stg_orders': [filter, mutate], 'stg_stores': [mutate], 'usa_stores': [filter], 'store_revenue': [join, join, aggregate]}

steps = by_model["store_revenue"]
ibis_anim.render(steps[:2], "store_revenue_joins", facts=facts)                         # the 2 joins only
ibis_anim.render(by_model["stg_stores"] + by_model["usa_stores"], "stores", facts=facts)  # 2 models in one video
```

- Always give `facts=facts`. Without it, the facts come from the given steps only. Each input of the first step is then a source with no facts, and the generated rows ignore the filters before it.
- The steps of the video count from 1. The trace and the program graph show the given steps only.
- `render` renders also when `IBIS_ANIM` is `0`. It writes no key, so the next `dbt` command does not skip a video that `render` wrote.

### Render a large project in chunks

A render takes about 20 seconds for each step at `low`, and about 2.5 times as long at `high`. Thus one video of a project with 100 steps takes more than half an hour at `low`. Do not render a large project in one video. Render it in chunks: one video for each model, and one video for each block of steps of a large model.

The script `./chunks.py` is in the folder of this skill. Run it with the Python of the project. Run each command from a folder of the dbt project.

1. List the models of the trace and their steps. This renders nothing and takes a few seconds:

   ```sh
   IBIS_ANIM=0 python chunks.py models/marts/store_revenue.py
   ```

   The output has one line for each ibis model, in the order that the models ran:

   ```
   stg_orders: 2 steps: filter, mutate
   stg_stores: 1 step: mutate
   usa_stores: 1 step: filter
   store_revenue: 3 steps: join, join, group_by → aggregate
   ```

   SQL models, seeds and sources have no steps, so they are not in the list. Multiply the steps of a model by 20 seconds to get its render time at `low`.

2. Render one model at `low` to check it: `IBIS_ANIM_QUALITY=low python -m ibis_anim dbt store_revenue`. Look at its frames, see [Look at a render](#look-at-a-render).

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

## Look at a render

An agent cannot watch a video. To check a render, save frames as PNG files and read the images:

1. Render at low quality: `IBIS_ANIM_QUALITY=low python -m ibis_anim my_script.py`.
2. Run the script `./frames.py`, which is in the folder of this skill, with the Python of the project. Give it the folder of the clips as its argument, for example `media/videos/480p15/sections`.
3. Read the PNG files in the folder `frames/` beside the clips. The last frame of each clip shows the end state of its step.

The script `./frames.py` saves one frame every 2 seconds and the last frame of each clip. Use `--every SECONDS` to change the interval, and `--out FOLDER` to change the folder. It uses PyAV and Pillow, which manim installs.

[The video layout](./video.md) describes each part of the screen and the animation of each operation.

## Problems

| Symptom | Cause | Action |
| --- | --- | --- |
| A video renders after each test run. | Each import of `ibis_anim` registers a render at exit. | Set `IBIS_ANIM=0`. |
| The video shows "no ibis operations". | No table method ran while the recording was on. | Make sure that the code calls table methods, and that it is not inside `recording(None)`. |
| stderr shows `ibis_anim: plain animation for step ...` | The animation of that operation does not handle this shape of call. | The step uses a plain animation. The video is still correct. Report the call to the developers of ibis-anim. |
| A step shows `no facts for this step`. | The facts rules do not read the SQL of that step. | The step has no facts. The other steps are not affected. |
| Steps are missing or mixed. | The recording is single-threaded. | Run the ibis code in one thread. |
| The dbt command prints `is up to date`, but the video is old. | The key covers the files of the model, the files upstream of it, the SQL and YAML files and `ibis_anim`. A change elsewhere, for example in a Python module that the model imports, does not change the key. | Add `--force`. |
| The dbt command prints `no ibis model NAME`. | No file `NAME.py` or `NAME.ibis` is in the model folders of the project, or the current folder is outside the project. | Give the path of the model file, or run the command from a folder of the project. |
| With `--deps`, the computer runs out of memory. | Each model renders in a process of its own. | Use `--jobs 2` or a lower number. |

## Limits

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The values of a result come from these rows only.
- The window animation draws row frames only. It does not draw range frames.
- On DuckDB and in-memory tables, the render computes each step once into a temporary table, and keeps these tables in memory until the process ends. A pipeline with large intermediate results needs that memory. On other backends, each count runs the pipeline from its sources, which is slow for a long pipeline.
