---
description: Change or extend the ibis-anim library itself, for example to record a new ibis method, animate an operation, add a facts rule or a warning, or improve the generated sample rows. Use when you edit the files in the ibis_anim package of a clone of ibis-anim.
flavors: [develop]
---

# Develop ibis-anim

This skill is for changes to the `ibis_anim` package. To use the library in a project, use the `ibis-anim` skill.

Read [the architecture](./architecture.md) before you change code. It names the building block that owns each responsibility, and the data that each block gives to the next one. Then follow the procedure for your change in [the recipes](./recipes.md).

## Project rules

- Use the virtual environment of the clone: `.venv/Scripts/python` on Windows, `.venv/bin/python` on other systems.
- Set `IBIS_ANIM=0` for tests and quick runs. Each import of `ibis_anim` registers a render at exit. Set `PYTHONIOENCODING=utf-8`, because the output has `→`, `⚠` and `×`.
- Each test file in `tests/` is a set of plain `assert` functions with a `__main__` runner that prints `ok`. Do not add a test framework or a fixture.
- When an animation changes, update the operation table in `README.md`.
- Keep the stages apart. `_scope.py`, `_facts.py`, `_derive.py` and `_samples.py` do not import manim. Test them without a render.
- The facts never come from data. A rule that is not certain gives no fact. A missing fact is acceptable. A wrong fact is a defect.
- A derived table name always has a space. `chains()` and the lineage in `_derive.py` treat a name that is a Python identifier as a variable of the program.

## Workflow

1. Find the building block that owns the change in [the architecture](./architecture.md).
2. Write a check in the test file of that block. Run it and make sure that it fails.
3. Make the change. Follow the recipe for the change.
4. Run all checks:

   ```sh
   export IBIS_ANIM=0 PYTHONIOENCODING=utf-8
   for t in tests/test_*.py; do .venv/Scripts/python "$t"; done
   ```

   To run one check: `.venv/Scripts/python -c "from tests.test_scope import test_chains_group_one_statement as t; t()"`.

5. If the change affects the video, render an example at low quality and look at its frames. See [Check a render](#check-a-render).
6. If an animation changed, update `README.md`.

## Check a render

1. Render a small example: `IBIS_ANIM_QUALITY=low .venv/Scripts/python -m ibis_anim examples/chain.py`. The `examples/` folder has one script for each group of operations.
2. Check stderr. The line `ibis_anim: plain animation for step ...` means that a choreography raised an exception and the step used the fallback animation.
3. Save frames of the clips with the frame script of the `ibis-anim` skill, `skills/ibis-anim/frames.py`. This path is relative to the folder of this skill. Give the script the folder `media/videos/480p15/sections`.
4. Read the PNG files. Check that no text overlaps, that each table stays inside the stage, and that the end state of each step is correct.

Renders of different videos can run in parallel. Each video has its own scene class and its own text cache.
