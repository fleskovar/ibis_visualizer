"""Render with manim 0.18 on a machine without ffprobe: read the clip metadata with PyAV instead.

For a compatibility check only. manim 0.18 runs `ffprobe` by its name to make the section index; a real
install has it with FFmpeg. The script needs `av` in the environment, and `ffmpeg` on the PATH.

usage: python manim018.py FILE[:FUNCTION] [ARGS...]     (the same arguments as `python -m ibis_anim`)
"""

import runpy
import sys

import av
import manim.scene.section as section


def get_video_metadata(path) -> dict:
    """The fields that manim 0.18 reads from ffprobe, for the first video stream of the clip."""
    with av.open(str(path)) as clip:
        stream = clip.streams.video[0]
        rate = stream.average_rate
        return {"width": stream.width, "height": stream.height, "nb_frames": str(stream.frames),
                "duration": str((clip.duration or 0) / 1e6), "avg_frame_rate": f"{rate.numerator}/{rate.denominator}",
                "codec_name": stream.codec_context.name}


section.get_video_metadata = get_video_metadata  # section.py imports the function by name
sys.argv = ["ibis_anim", *sys.argv[1:]]
runpy.run_module("ibis_anim", run_name="__main__")
