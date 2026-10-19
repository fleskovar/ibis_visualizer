"""Drop-in replacement for `ibis` that animates the table operations of a script.

Replace `import ibis` with `import ibis_anim as ibis`. The script runs as before. At exit, a
manim video of each ibis step goes to `media/`. Set `IBIS_ANIM=0` to run without the video.
"""

import atexit
import concurrent.futures.thread  # noqa: F401  the video renders at exit, where a first import of it raises: pyarrow 14 imports pandas, which imports it
import importlib
import importlib.abc
import importlib.util
import os
import sys
from pathlib import Path

import ibis
from ibis import *  # noqa: F403

from ibis_anim import _derive, _tracer
from ibis_anim._dbt import load_model as load_dbt_model
from ibis_anim._dbt import model_steps
from ibis_anim._facts import declare
from ibis_anim._scope import table_names
from ibis_anim._scene import render, visualize
from ibis_anim._tracer import Step, install, recording, trace

__all__ = [*ibis.__all__, "Step", "declare", "load_dbt_model", "model_steps", "recording", "render", "report", "trace",
           "visualize"]


def report(func, *args, **kwargs) -> str:
    """Trace `func` and return what each step guarantees about its columns, with warnings."""
    _, steps = trace(func, *args, **kwargs)
    names = table_names(steps)
    return _derive.report(steps, _derive.derive(steps, names), names)


def __getattr__(name):
    return getattr(ibis, name)


def __dir__():
    return sorted(set(globals()) | set(dir(ibis)))


class _SubmoduleAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Make `import ibis_anim.selectors` give the module `ibis.selectors`."""

    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(f"{__name__}.") and not fullname.startswith(f"{__name__}._"):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        module = importlib.import_module("ibis" + spec.name.removeprefix(__name__))
        spec.loader_state = module.__spec__
        return module

    def exec_module(self, module):
        module.__spec__ = module.__spec__.loader_state  # the import system replaced the spec of the real module


def _render_at_exit(steps: list[Step], name: str | None = None, facts: dict | None = None) -> Path | None:
    """Print the warnings of the steps, render them and return the video. `facts` come from a larger trace, else from
    the steps."""
    if steps and os.environ.get("IBIS_ANIM", "1") != "0":
        names = table_names(steps)
        facts = _derive.derive(steps, names) if facts is None else facts
        for line in _derive.report(steps, facts, names).splitlines():
            if "⚠" in line:
                print(f"ibis_anim: {line.strip()}")
        video = render(steps, name or Path(sys.argv[0]).stem or "ibis_anim", facts=facts)
        print(f"ibis_anim: video at {video}")
        return video
    return None


sys.meta_path.insert(0, _SubmoduleAlias())
install()
_script_steps: list[Step] = []
_tracer._steps = _script_steps  # record the whole script; `trace` and `visualize` record apart
atexit.register(_render_at_exit, _script_steps)
