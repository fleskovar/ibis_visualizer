## ibis-anim

`ibis_anim` is a drop-in replacement for the `ibis` module. It records each ibis table call of a program and renders a manim video that looks like a debugger.

- To animate a script, replace `import ibis` with `import ibis_anim as ibis`, or run `python -m ibis_anim FILE[:FUNCTION]` without a change to the code.
- To animate one dbt-ibis model, run `python -m ibis_anim dbt MODEL`. `MODEL` is the model file or the model name. Add `--deps` to get one video for each upstream ibis model too. The command does not render a video again if its inputs did not change. Add `--force` to render it again.
- Do not render a large dbt-ibis project in one video: a render takes about 20 seconds for each step at `low`. Render it in chunks, as "Render a large project in chunks" in the `ibis-anim` skill tells you.
- The video goes to `media/<name>.mp4`. The clip of each step goes to `media/videos/<resolution>/sections/`.
- Set `IBIS_ANIM=0` for tests and quick runs. Otherwise each import of `ibis_anim` renders a video at exit.
- Set `IBIS_ANIM_QUALITY=low` to check a render. The default `high` (1080p) is slow.

Use the `ibis-anim` skill for the full instructions. To change the library itself, use the `ibis-anim-develop` skill. It installs with the `develop` flavor of the hcm bundle `ibis-anim`.
