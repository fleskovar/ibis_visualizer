---
description: Render a debugger video of ibis code with ibis-anim, from Python or with the command line `python -m ibis_anim FILE[:FUNCTION]`, read the facts and warnings that it computes, and look at the frames of a render. Covers the caveats of dbt-ibis models. Use when the user asks to visualize, animate, explain or debug an ibis pipeline, one function of a file, a dbt-ibis model or a script that imports ibis_anim.
flavors: [use, develop]
---

# Use ibis-anim

`ibis_anim` records each ibis table call of a program while the program runs. At exit, it renders a manim video with one section for each call. The video shows the code, the call stack, the tables in scope, an animation of the operation on the first rows of its inputs, and the facts that the step guarantees.

`ibis_anim` is a drop-in replacement for `ibis`. It re-exports every name and submodule of `ibis`, and it wraps the methods of the ibis table classes in place. The program gives the same results with and without it.

## Install

Install the package `ibis-anim` in the Python environment of the project. For dbt-ibis models, install the extra `ibis-anim[dbt]`. The package needs Python 3.11 or later, `ibis-framework[duckdb]` 10 or later, and `manim` 0.19 or later. If `import manim` fails, follow the manim installation guide for the platform.

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
```

The command has three forms:

| Form | What runs | What the video shows | Video file |
| --- | --- | --- | --- |
| `python -m ibis_anim FILE [ARGS...]` | The file runs as a script, the same as `python FILE ARGS`. | Each table call of the whole run. | `media/<file name without extension>.mp4` |
| `python -m ibis_anim FILE:FUNCTION [ARGS...]` | The module-level code of the file runs. Then the command calls `FUNCTION` without arguments. | Only the table calls in `FUNCTION` and in the functions that it calls. | `media/<FUNCTION>.mp4` |
| `python -m ibis_anim MODEL_FILE` | A dbt-ibis model and the ibis models upstream of it. See [dbt-ibis models](#dbt-ibis-models). | Each table call of each model. | `media/<model name>.mp4` |

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

`render(steps, name, output_dir="media", quality=None)` takes the same values as arguments. Most of the time goes to manim, about 30 seconds for each step at `low`: the example `customer_health.py`, with 22 steps, takes about 10 minutes at `low`. Use `low` to check a change, and `high` for the final video.

## The API

| Name | Use |
| --- | --- |
| `trace(func, *args, **kwargs)` | Call `func` and return `(result, steps)`. No video. |
| `visualize(func, *args, **kwargs)` | Trace `func` and render its video. Returns the path. |
| `render(steps, name, output_dir="media", quality=None)` | Render a list of steps. Returns the path. |
| `report(func, *args, **kwargs)` | Trace `func` and return the facts and warnings of each step as text. No video. |
| `declare(table, key=None, values=None, not_null=(), references=None, unique=())` | State constraints of a source table. See [Facts](#facts-and-warnings). |
| `recording(steps)` | A context manager. `recording(None)` turns off the recording inside the block. |
| `load_dbt_model(path)` | The `model` function of a dbt-ibis file. |
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

**Each upstream model runs.** Each ibis model upstream of the model runs again in the process, and each step of each model is in the video. A large project gives a long video. To show fewer steps, render a model nearer to the sources.

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

## Limits

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The values of a result come from these rows only.
- The window animation draws row frames only. It does not draw range frames.
- On DuckDB and in-memory tables, the render computes each step once into a temporary table, and keeps these tables in memory until the process ends. A pipeline with large intermediate results needs that memory. On other backends, each count runs the pipeline from its sources, which is slow for a long pipeline.
