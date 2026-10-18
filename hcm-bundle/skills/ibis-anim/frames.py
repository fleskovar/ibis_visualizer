"""Save frames of ibis-anim clips as PNG files, so that an agent or a person can look at a render.

    python frames.py PATH [--every SECONDS] [--out FOLDER]

PATH is one .mp4 file or a folder of them, for example media/videos/480p15/sections. The script saves
one frame each SECONDS (default 2) and the last frame of each clip, as <clip>_<seconds>s.png in FOLDER
(default: frames/ beside each clip). It uses PyAV and Pillow, which manim installs.
"""

import argparse
from pathlib import Path

import av


def save_frames(clip: Path, every: float, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    saved, next_time, last = [], 0.0, None
    with av.open(str(clip)) as container:
        for frame in container.decode(video=0):
            last = frame
            if frame.time is not None and frame.time >= next_time:
                saved.append(_save(frame, clip, out))
                next_time += every
    if last is not None:
        saved.append(_save(last, clip, out))  # the end state of the step
    return list(dict.fromkeys(saved))


def _save(frame, clip: Path, out: Path) -> Path:
    path = out / f"{clip.stem}_{frame.time or 0:06.2f}s.png"
    frame.to_image().save(path)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path)
    parser.add_argument("--every", type=float, default=2.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    clips = sorted(args.path.glob("*.mp4")) if args.path.is_dir() else [args.path]
    if not clips:
        raise SystemExit(f"no .mp4 file in {args.path}")
    for clip in clips:
        for path in save_frames(clip, args.every, args.out or clip.parent / "frames"):
            print(path)


if __name__ == "__main__":
    main()
