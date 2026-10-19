---
description: Rules for the manim scene of ibis-anim
appliesTo: ["ibis_anim/_scene.py"]
flavors: [develop]
---

- The height of the top bar changes with each statement. Read `self.zone_top`, `self.caption_y`, `self.strip_y`, `self.bar_bottom` and `self.trace_rows`, which `_layout(i)` sets for each step. Do not use the module constants for these values.
- Add each mobject that must stay on the screen to `_persistent()`. Otherwise `_settle` and `_finish` remove it.
- manim `Text` drops leading spaces. To indent a line, move it by the indent times `_char_width(...)`.
- End each choreography with `self._settle(...)` and `self.result = <the result grid>`. If a choreography cannot draw a call, raise an exception: `_animate` then draws the step with `_flow`.
- Run a table on other rows with `_substitute` and `self.computed`, through `_binder`. Do not call `Node.replace` or run a count on the full plan: each query must read the tables of `_materialize`.
- After a change, render an example with `IBIS_ANIM_QUALITY=low` and look at the frames of its clips.
