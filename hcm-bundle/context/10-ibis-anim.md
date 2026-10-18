## ibis-anim

`ibis_anim` is a drop-in replacement for the `ibis` module. It records each ibis table call of a program and renders a manim video that looks like a debugger.

- To animate a script, replace `import ibis` with `import ibis_anim as ibis`, or run `python -m ibis_anim FILE[:FUNCTION]` without a change to the code.
- The video goes to `media/<name>.mp4`. The clip of each step goes to `media/videos/<resolution>/sections/`.
- Set `IBIS_ANIM=0` for tests and quick runs. Otherwise each import of `ibis_anim` renders a video at exit.
- Set `IBIS_ANIM_QUALITY=low` to check a render. The default `high` (1080p) is slow.

Use the `ibis-anim` skill for the full instructions. To change the library itself, use the `ibis-anim-develop` skill. It installs with the `develop` flavor of the hcm bundle `ibis-anim`.
