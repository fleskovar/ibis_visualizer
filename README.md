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

When a step changes the type of a column (`cast`, `try_cast`, or a `mutate` with `.cast(...)`), the type cell changes after the values convert, for example `float64 → int64`.

Columns that the arguments read are yellow. New columns are green. A column that you build outside the table, for example `points = (t.score * 10).cast("int64")`, shows its formula when `mutate` or `select` puts it into a table.

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

## Use

```sh
python examples/orders_pipeline.py   # filter, mutate, join, aggregate, one branch
python examples/pivots.py            # pivot_wider, mutate, pivot_longer
python examples/rolling.py           # rolling sum, cumulative sum, order_by, limit
python examples/table_ops.py         # union, distinct, fill_null, cast, new columns, select, rename, unnest, intersect
python examples/customer_health.py   # a realistic job: 5 tables, chains, a loop, a branch, 2 left joins, a rank (22 steps)
IBIS_ANIM=0 python my_script.py      # run without the video
python tests/test_tracer.py          # checks of the tracer
python tests/test_scope.py           # checks of the tables panel plan
python tests/test_facts.py           # checks of the facts and warnings
```

To render one function only, call `ibis_anim.visualize(func, *args)`. To get the steps without a video, call `ibis_anim.trace(func, *args)`.

## Limits of the MVP

- The trace shows the path that ran. It does not show branches that did not run.
- Each grid shows the first 6 rows and at most 6 columns. The result values come from these rows only.
- When an animation cannot handle a call, the step uses the plain column animation and prints the reason on stderr.
- Recording is single-threaded.
- The window animation draws row frames only. It does not draw range frames.
