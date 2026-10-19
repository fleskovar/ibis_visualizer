# Set up ibis-anim in a project with numpy 1.x

Use this setup when the project must keep numpy 1.x, for example because it uses pyarrow 14 or another pyarrow before 16. These pyarrow versions are compiled against numpy 1.x and do not run with numpy 2.

ibis-anim itself works with numpy 1.x. The limit comes from manim: manim 0.19 and later need numpy 2. manim 0.18.1 is the last version that allows numpy 1.x, and ibis-anim accepts it.

The tested set is Python 3.11.4, pyarrow 14.0.2, numpy 1.26.4, manim 0.18.1, ibis-framework 12.0.0 and sqlglot 30.17.0.

## What is different from the usual setup

| Item | Usual setup | numpy 1.x setup |
| --- | --- | --- |
| Python | 3.11 or later | 3.11 or 3.12 only. manim 0.18 and pyarrow 14 have no wheels for later versions. |
| numpy | 2.x | below 2. Add `numpy<2` to the requirements of the project. |
| manim | 0.22 or the latest | 0.18.1 |
| FFmpeg | not needed | the `ffmpeg` and `ffprobe` programs must be on the PATH |

## Procedure

1. Make a virtual environment with Python 3.11 or 3.12.

   ```sh
   py -3.11 -m venv .venv          # Windows
   python3.11 -m venv .venv        # macOS and Linux
   ```

2. Add `numpy<2` to the requirements of the project, next to the pyarrow version. pyarrow 14 does not declare that it needs numpy 1.x. Without `numpy<2`, pip installs numpy 2 and manim 0.22, and `import pyarrow` then fails. To make the choice of manim explicit too, add `manim==0.18.1`.

3. Install ibis-anim with the requirements of the project. Use the Python of the environment:

   ```sh
   .venv/Scripts/python -m pip install ibis-anim "pyarrow==14.0.2" "numpy<2"   # Windows
   .venv/bin/python -m pip install ibis-anim "pyarrow==14.0.2" "numpy<2"       # macOS and Linux
   ```

   For dbt-ibis models, install `ibis-anim[dbt]` instead of `ibis-anim`. On Linux, if pip builds `pycairo` or `ManimPango` from source and fails, install the cairo and pango development packages (`libcairo2-dev`, `libpango1.0-dev`) and try again.

4. Install FFmpeg, and put its `ffmpeg` and `ffprobe` programs on the PATH. See [External programs](#external-programs).

5. Check the versions and the programs:

   ```sh
   python -c "import sys, numpy, pyarrow, manim, ibis; print(sys.version.split()[0], numpy.__version__, pyarrow.__version__, manim.__version__, ibis.__version__)"
   python -m pip check
   ffmpeg -version
   ffprobe -version
   ```

   The first command must show Python 3.11 or 3.12, numpy 1.x, your pyarrow version and manim 0.18.1. `pip check` must report `No broken requirements found.`

6. Render a small script at low quality, and check stderr:

   ```sh
   IBIS_ANIM_QUALITY=low python -m ibis_anim my_script.py
   ```

   The last line must be `ibis_anim: video at media/my_script.mp4`. No line must say `plain animation`. Then look at the frames, as [the skill](./SKILL.md#look-at-a-render) describes.

## External programs

pip installs all the Python packages, but not the programs below. Install them with the package manager of the system, and make sure that they are on the PATH.

| Program | Needed | Why | Check |
| --- | --- | --- | --- |
| `ffmpeg` | with manim 0.18 | manim 0.18 writes each clip and the video with it. | `ffmpeg -version` |
| `ffprobe` | with manim 0.18 | manim 0.18 reads each clip with it, to make the section index in `media/videos/<resolution>/sections/`. | `ffprobe -version` |
| cairo and pango libraries | on Linux, only if pip builds `pycairo` or `ManimPango` from source | These packages draw the text and the shapes. On Windows and macOS, their wheels contain the libraries. | `pip install` of manim succeeds |

The current setup, with manim 0.19 or later, needs none of these programs: manim writes the video through PyAV, and the PyAV wheels contain the FFmpeg libraries. No setup needs LaTeX: ibis-anim draws all text with Pango, not with `Tex` or `MathTex`.

### Install FFmpeg

`ffmpeg` and `ffprobe` come together in each FFmpeg build. Install one of these:

| System | Command | PATH |
| --- | --- | --- |
| Windows | `winget install Gyan.FFmpeg` | winget adds the programs to the PATH. |
| Windows | `choco install ffmpeg` | Chocolatey adds the programs to the PATH. |
| Windows | a zip file from `https://www.gyan.dev/ffmpeg/builds/` or `https://github.com/BtbN/FFmpeg-Builds/releases` | Extract it, for example to `C:\tools\ffmpeg`, and add its `bin` folder to the PATH (see below). |
| macOS | `brew install ffmpeg` | Homebrew puts the programs in a folder that is on the PATH. |
| Debian, Ubuntu | `sudo apt install ffmpeg` | apt puts the programs in `/usr/bin`. |
| Fedora | `sudo dnf install ffmpeg` (from RPM Fusion) | dnf puts the programs in `/usr/bin`. |
| a conda environment | `conda install -c conda-forge ffmpeg` | The programs go into the environment. They are on the PATH while the environment is active. |

The pip package `imageio-ffmpeg` is not sufficient: it has an `ffmpeg` binary, but not `ffprobe`.

On Linux, if pip builds `pycairo` or `ManimPango` from source and fails, install the development packages, then run the pip install again:

```sh
sudo apt install libcairo2-dev libpango1.0-dev pkg-config python3-dev    # Debian and Ubuntu
sudo dnf install cairo-devel pango-devel pkg-config python3-devel        # Fedora
```

### Put a program on the PATH

Do this only if the install did not put the programs on the PATH, for example after you extract a zip file.

- **Windows.** Add the `bin` folder to the PATH of the user, in PowerShell:

  ```powershell
  $bin = "C:\tools\ffmpeg\bin"
  [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";$bin", "User")
  ```

  Or use **Settings › System › About › Advanced system settings › Environment Variables**, and add the folder to `Path` under the user variables.
- **macOS and Linux.** Add this line to `~/.zshrc` or `~/.bashrc`:

  ```sh
  export PATH="/opt/ffmpeg/bin:$PATH"
  ```

A program reads the PATH when it starts. After a change, close and open the terminal, and restart the IDE or the agent that runs the render. A terminal that was open before the change does not find the programs.

### Check the PATH

Run these commands in a new terminal, with the environment of the project active:

```sh
where.exe ffmpeg ffprobe        # Windows, PowerShell or cmd
command -v ffmpeg ffprobe       # macOS, Linux, Git Bash
ffmpeg -version
ffprobe -version
```

Each command must give a path or a version. In Git Bash, a PATH entry written as `C:/...` breaks at its colon: write it as `/c/...`.

To use an `ffmpeg` that is not on the PATH, give its path in a `manim.cfg` file in the folder where the render runs. `ffprobe` must still be on the PATH, because manim 0.18 always runs it by its name.

```ini
[ffmpeg]
ffmpeg_executable = C:\tools\ffmpeg\bin\ffmpeg.exe
```

## Problems

| Symptom | Cause | Action |
| --- | --- | --- |
| `import pyarrow` prints `A module that was compiled using NumPy 1.x cannot be run in NumPy 2...`, then `ImportError: numpy.core.multiarray failed to import`. | numpy 2 is installed. | Add `numpy<2` to the requirements, and install again. |
| pip installs manim 0.22 or numpy 2. | The requirements do not have `numpy<2`, or the Python is 3.13 or later. | Add `numpy<2`. Use Python 3.11 or 3.12. |
| `No matching distribution found for pyarrow==14.0.2`, or for `manim==0.18.1`. | The Python is 3.13 or later. | Make the environment with Python 3.11 or 3.12. |
| `import manim` warns `Couldn't find ffmpeg or avconv`. | `ffmpeg` is not on the PATH. The render then fails. | Install FFmpeg, and open a new terminal. See [External programs](#external-programs). |
| The render stops with `RuntimeError: Manim could not find ffmpeg, which is required for generating video output.` | `ffmpeg` is not on the PATH, and `manim.cfg` does not give its path. | Install FFmpeg, or put its `bin` folder on the PATH. Check with `where.exe ffmpeg` or `command -v ffmpeg`. |
| The render stops with `FileNotFoundError` in `get_video_metadata`. | `ffprobe` is not on the PATH. This happens with `imageio-ffmpeg`, which has no `ffprobe`, and with a `manim.cfg` that gives the path of `ffmpeg` only. | Install a full FFmpeg, and put its `bin` folder on the PATH. |
| The programs work in one terminal, but the render does not find them. | The IDE, the agent or the terminal started before the PATH changed. | Restart it. |
| The render stops with `Error opening output file ...<module>...mp4`. | An older ibis-anim on Windows: manim 0.18 keeps `<module>` in the clip name of a step at module level. | Update ibis-anim. |
| The render at exit prints `RuntimeError: can't register atexit after shutdown`, and some steps say `plain animation`. | An older ibis-anim. On Python 3.11, pyarrow 14 imports pandas while the interpreter shuts down, and that import fails. | Update ibis-anim. The current version imports the module that pandas needs before the render. |
