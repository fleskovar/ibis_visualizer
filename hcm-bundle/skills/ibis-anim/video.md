# The video layout

The video has one section for each recorded table call. It starts with a title section, `00_overview`. Each clip has the name `<video>_<NNNN>_<NN>_<function>_<operation>.mp4`, for example `pipeline_0002_02_pipeline_mutate.mp4`. `NNNN` is the section index of manim, and `NN` is the step number.

## The parts of the screen

| Part | Place | Content |
| --- | --- | --- |
| Top bar | top | The operation, the call path (`customer_health › clean()`), the step counter, and the whole current statement with its line numbers. The line of the current call is lit. A statement of more than 6 lines shows the 6 lines around the current call. |
| Trace | left | Each function call with the tables that it reads, its steps under it, and a line for each table that it hands on. Done steps have ✔, the current step has ▸, and the next steps are grey. A step that will warn has ⚠ before it runs. |
| Pipeline | above the stage | The current statement as a flow, for example `items → join(products, "sku") → mutate(…) → revenue`. The current call is lit. |
| Stage | middle | The animation of the step. Each table has a facts row and a type row above its header. |
| Caption | under the pipeline | One line for each phase of the animation, then the facts that the step guarantees. |
| Program | bottom left | The program as a graph: the source tables, each function call and the result. The current call is yellow. The calls that ran are green. |
| Tables in scope | bottom right | A card for each table in scope, with its name, its shape and its first rows. The inputs of the step light up yellow and grow. The result shrinks into a green card. |

## Names and scope

- A table has the name of the first variable that holds it. A table that no variable holds gets a name from its operation: `filtered orders`, `orders × stores`, `orders with total`, `revenue by region`. The result of a dbt-ibis model has the name of the model.
- When a result rebinds a variable (`orders = orders.filter(...)`), the result card takes the place of the input card.
- When the next step reads the result, the result stays on the stage and moves into the next step.
- In a chain (`t.join(...).mutate(...).aggregate(...)`), each call runs on the rows that the call before it showed. Only the last result of the chain goes to the shelf.
- A table that no later step reads gets small and dim. Two steps after its last use, it leaves the shelf.

## Colours

- Columns that the arguments read are yellow. New columns are green.
- Rows that a step removes turn red and leave.
- Warnings show in red boxes at the bottom of the stage.
- The facts that a step changes light up in the facts row.

## The animation of each operation

| Operation | Animation |
| --- | --- |
| `filter` | A label shows the condition (`keep rows where amount > 100`). Each row gets ✔ or ✘. The failed rows turn red and leave. The other rows slide up. |
| `filter` with several tests (`ibis.and_`, `ibis.or_`, `&`, `\|`) | Each test gets a column of ✔/✘. Then, for each row, the marks merge into the result column (`1 AND (2 OR 3)`), and the failed rows leave. A null test shows `–` and does not keep the row. |
| `ibis.ifelse` in `mutate` or `select` | A legend shows the two branches. For each row, the row gets ✔/✘, and the value of the chosen branch moves into the new cell. |
| `ibis.coalesce` in a new column | A pointer reads the arguments of each row from left to right. A null sends it to the next argument. The first value that is not null moves into the new cell. |
| `distinct` | A scanner moves down the rows below the current row. The rows with the same values turn red and leave. |
| `drop_null`, semi and anti `join` | The same as `filter`, with the test of the operation. |
| `limit`, `head`, `sample` | A cut line or a count, then the removed rows leave. |
| `order_by` | The sort column is yellow, an arrow shows the direction, and the rows move along arcs to their new positions. |
| `mutate`, `select`, `cast` | A caption shows the formula of each new column. For each row, the source cells move into the new cell. |
| `select`, `drop`, `rename`, `relocate` | Dropped columns turn red. Kept columns move to their new position. Renamed headers change their text. |
| window functions | The rows take the colour of their partition and are sorted. Then a frame moves down the rows. |
| `group_by` → `aggregate` | The rows take the colour of their group and come together. Then each group collapses into one result row. |
| `join` | The join columns move to the inner edges and the tables come together. The rows line up on equal keys, and the key columns merge. |
| `pivot_wider` | Distinct names move into column headers. Each value moves to its cell. |
| `pivot_longer` | For each row, the pivoted cells rotate into a vertical column of the result. |
| `union` | The rows of the second table go under the rows of the first. With `distinct=True`, the repeated rows leave. |
| `intersect`, `difference` | A line joins each row of the first table to the equal row of the second table. The rows that do not stay leave. |
| `fill_null` | The null cells light up red and take the replacement value. |
| `unnest` | Each array cell splits into its elements, and each element moves to its own row. |
| other operations | Each result column comes from the input column with the same name. |

## Columns built outside a table call

A column that the program builds outside a table call, and holds in a variable, is a step of its own:

```python
label = ibis.coalesce(players.nick, players.player)
players = players.mutate(label=label)
```

This code gives two steps. The `column` step is at the line of the assignment. It makes a one-column table `label`, which goes to the shelf as "a column of players". At the `mutate`, each cell of `label` moves into the new column. An expression that the program writes in the call itself (`mutate(x=t.a + 1)`) is part of that step.
