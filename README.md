# ibis-anim

A visual debugger for [ibis](https://ibis-project.org) code. It renders each table operation as a short [manim](https://www.manim.community) animation.

`ibis_anim` is a drop-in replacement for the `ibis` module. Change the import and run the script:

```python
import ibis_anim as ibis          # was: import ibis
from ibis_anim import _           # was: from ibis import _
import ibis_anim.selectors as s   # was: import ibis.selectors as s
```

The script runs as before. At exit, the video goes to `media/videos/480p15/<script>.mp4`. There is also one clip per step in `media/videos/480p15/sections/`.

## How it works

1. On import, `ibis_anim` wraps the methods of `ibis.Table` and `GroupedTable` (`filter`, `mutate`, `join`, `aggregate`, `pivot_wider`, ...).
2. Each call records a step: the op, the input and output tables, the referenced columns, the Python call stack and the source text of the call. The ibis expressions are lazy, so nothing executes at this point.
3. Python control flow needs no special handling. An `if` records only the branch that ran. A loop records one step per iteration.
4. At exit, each step runs again on DuckDB, on the first 6 rows of each input only. Thus each value in the animation comes from the rows on the screen.
5. Each kind of operation has its own animation, in phases. A caption under the code explains each phase.

| Operation | Animation |
| --- | --- |
| `filter` | Each row gets ✔ or ✘. The failed rows turn red and leave. The other rows slide up. |
| `distinct`, `drop_null`, semi and anti `join` | The same as `filter`, with the test of the operation. |
| `limit`, `head`, `sample` | A cut line or a count, then the removed rows leave. |
| `order_by` | The sort column is yellow, an arrow shows the direction, and the rows move along arcs to their new positions. |
| `mutate`, `select`, `cast` | A caption shows the formula of each new column. For each row, the source cells move into the new cell, with a short delay between rows. |
| `select`, `drop`, `rename`, `relocate` | Dropped columns turn red. Kept columns move to their new position. Renamed headers change their text. |
| window functions | The rows take the colour of their partition and are sorted. Then a frame moves down the rows and shows the rows of each value. |
| `group_by` → `aggregate` | The rows take the colour of their group and come together. Then each group in turn collapses into one result row. |
| `join` | The join columns move to the inner edges and the tables come together. The rows line up on equal keys, and the key columns overlap and merge. |
| `pivot_wider` | Distinct names move into column headers. The id columns become one row per value. The empty cells light up, then each value moves to its cell. |
| `pivot_longer` | For each row, the pivoted cells rotate into a vertical column of the result. |
| `union`, `intersect`, `difference` | Union puts the second table under the first. Intersect and difference test each row against the other table. |
| `fill_null` | The null cells light up red and take the replacement value. |
| `unnest` | Each array cell splits into its elements, and each element moves to its own row. |
| other ops | Each result column comes from the input column with the same name. |

When a step changes the type of a column (`cast`, `try_cast`, or a `mutate` with `.cast(...)`), the grid shows a type row above the header. After the values convert, the type cell changes, for example `float64 → int64`.

Columns that the arguments read are yellow. New columns are green. A column that you build outside the table, for example `points = (t.score * 10).cast("int64")`, shows its formula when `mutate` or `select` puts it into a table.

## Use

```sh
python examples/orders_pipeline.py   # filter, mutate, join, aggregate, one branch
python examples/pivots.py            # pivot_wider, mutate, pivot_longer
python examples/rolling.py           # rolling sum, cumulative sum, order_by, limit
python examples/table_ops.py         # union, distinct, fill_null, cast, new columns, select, rename, unnest, intersect
python examples/customer_health.py   # a realistic job: 5 tables, chains, a loop, a branch, 2 left joins, a rank (22 steps)
IBIS_ANIM=0 python my_script.py      # run without the video
python tests/test_tracer.py          # checks
```

To render one function only, call `ibis_anim.visualize(func, *args)`. To get the steps without a video, call `ibis_anim.trace(func, *args)`.

## Limits of the MVP

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The result values come from these rows only.
- When an animation cannot handle a call, the step uses the plain column animation and prints the reason on stderr.
- Recording is single-threaded.
- The window animation draws row frames only. It does not draw range frames.
