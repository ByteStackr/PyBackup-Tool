"""Regenerate the README images: python docs/make_demo.py

Runs the real tool against a throwaway project and renders the captured
output as terminal screenshots and an animated GIF in docs/images/.
Needs Pillow (the tool itself has no dependencies).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap

from PIL import Image, ImageDraw, ImageFont

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "docs", "images")

WIDTH, COLUMNS = 940, 100
FONT_SIZE, LINE_HEIGHT, PADDING, TITLE_BAR = 15, 21, 18, 34
BACKGROUND, BAR = (13, 17, 23), (33, 38, 45)
COLORS = {
    "prompt": (63, 185, 80),
    "command": (240, 246, 252),
    "output": (173, 186, 199),
    "good": (63, 185, 80),
    "warn": (210, 153, 34),
    "error": (248, 81, 73),
    "comment": (110, 118, 129),
}
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


def load_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default()


FONT = load_font()


class Demo:
    """Runs commands in a scratch directory and records (command, output lines)."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="pybackup-demo-")
        self.env = dict(os.environ, PYTHONPATH=REPO, PYTHONIOENCODING="utf-8",
                        PYBACKUP_CONFIG=os.path.join(self.root, ".settings.json"))

    def write(self, rel, content):
        path = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def tool(self, arguments):
        """Run ``pybackup-tool <arguments>`` and return a scene."""
        import shlex
        result = subprocess.run(
            [sys.executable, "-m", "pybackuptool"] + shlex.split(arguments),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
        text = result.stdout.replace(self.root + os.sep, "").replace(self.root, ".")
        return ("pybackup-tool " + arguments, text.replace("\\", "/").rstrip().split("\n"))

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def color_of(line):
    lowered = line.lower()
    if line.startswith("ERROR") or "failed" in lowered:
        return COLORS["error"]
    if line.startswith("WARNING") or "would delete" in lowered or "not in this backup" in lowered:
        return COLORS["warn"]
    if any(word in lowered for word in ("successfully", "is valid", "completed", "rebuilt",
                                        "checksums verified")):
        return COLORS["good"]
    return COLORS["output"]


def wrap(line):
    return textwrap.wrap(line, COLUMNS, subsequent_indent="    ",
                         drop_whitespace=False) or [""]


def render(lines, height_lines, title="pybackup-tool"):
    """lines: list of segment lists [(text, color), ...]"""
    height = TITLE_BAR + PADDING * 2 + LINE_HEIGHT * height_lines
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, WIDTH, TITLE_BAR], fill=BAR)
    for index, dot in enumerate([(248, 81, 73), (210, 153, 34), (63, 185, 80)]):
        draw.ellipse([14 + index * 20, 11, 26 + index * 20, 23], fill=dot)
    draw.text((WIDTH // 2, TITLE_BAR // 2), title, font=FONT, fill=COLORS["comment"], anchor="mm")
    y = TITLE_BAR + PADDING
    for segments in lines[-height_lines:]:
        x = PADDING
        for text, color in segments:
            draw.text((x, y), text, font=FONT, fill=color)
            x += draw.textlength(text, font=FONT)
        y += LINE_HEIGHT
    return image


def scene_lines(command, output, typed=None):
    """Display lines for one scene; ``typed`` limits how much of the command shows."""
    shown = command if typed is None else command[:typed]
    cursor = [] if typed is None else [("\u2588", COLORS["command"])]
    lines = [[("$ ", COLORS["prompt"]), (shown, COLORS["command"])] + cursor]
    if typed is None:
        color = COLORS["output"]
        for line in output:
            # Indented detail lines belong to the message above them.
            if not line.startswith("  "):
                color = color_of(line)
            for part in wrap(line):
                lines.append([(part, color)])
    return lines


def screenshot(name, scenes, title):
    lines = []
    for command, output in scenes:
        if lines:
            lines.append([])
        if command.startswith("#"):
            lines.append([(command, COLORS["comment"])])
        else:
            lines.extend(scene_lines(command, output))
    render(lines, len(lines), title).save(os.path.join(OUT, name), optimize=True)
    print("wrote", name, "(%d lines)" % len(lines))


def animation(name, scenes, height_lines):
    frames, durations = [], []

    # One palette for every frame, built from an image that uses all the colours;
    # per-frame palettes make colours flicker.
    sample = [[("sample ", color) for color in COLORS.values()]]
    palette = render(sample, height_lines).quantize(colors=96)

    def add(lines, milliseconds):
        frame = render(lines, height_lines)
        frames.append(frame.quantize(palette=palette, dither=Image.Dither.NONE))
        durations.append(milliseconds)

    for command, output in scenes:
        if command.startswith("#"):
            add([[(command, COLORS["comment"])]], 1600)
            continue
        for typed in range(0, len(command) + 1, 3):
            add(scene_lines(command, output, typed), 45)
        add(scene_lines(command, output, len(command)), 350)
        full = scene_lines(command, output)
        # Reveal the output a few lines at a time, then hold.
        for count in range(2, len(full), 3):
            add(full[:count], 110)
        add(full, 2600)

    frames[0].save(os.path.join(OUT, name), save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)
    print("wrote", name, "(%d frames, %.0f s)" % (len(frames), sum(durations) / 1000))


def main():
    os.makedirs(OUT, exist_ok=True)
    demo = Demo()
    try:
        demo.write("myapp/README.md", "# My App\n" + "Documentation line.\n" * 80)
        demo.write("myapp/requirements.txt", "flask\nrequests\n")
        demo.write("myapp/src/app.py", "def main():\n    return 'hello'\n" * 300)
        demo.write("myapp/src/models.py", "class User:\n    pass\n" * 200)
        demo.write("myapp/src/utils/helpers.py", "def helper():\n    return 1\n" * 150)
        demo.write("myapp/tests/test_app.py", "def test_main():\n    assert True\n" * 60)
        demo.write("myapp/.env", "API_KEY=secret\n")
        demo.write("myapp/debug.log", "log line\n" * 500)
        demo.write("myapp/node_modules/leftpad/index.js", "module.exports = 1;\n" * 400)
        demo.write("myapp/__pycache__/app.cpython-312.pyc", "x" * 4000)

        dry_run = demo.tool('backup myapp -d backups --dry-run -v')
        backup = demo.tool('backup myapp -d backups -n "Before refactor" -s "Stable state"')
        demo.write("myapp/src/app.py", "def main():\n    return 'v2'\n" * 320)
        demo.tool('backup myapp -d backups -n "After refactor"')
        listing = demo.tool("list -d backups")
        verify = demo.tool("verify latest -d backups")
        restore = demo.tool("restore latest restored -d backups")
        restore_again = demo.tool("restore latest restored -d backups")
        only = demo.tool('restore latest picked -d backups --only "src/utils/"')
        cleanup = demo.tool("cleanup -d backups --keep-count 1 --dry-run")

        index_path = os.path.join(demo.root, "backups", "backups_metadata.json")
        with open(index_path, "w") as handle:
            handle.write("{ damaged")
        broken = demo.tool("list -d backups")
        reindex = demo.tool("reindex -d backups")
        verify_all = demo.tool("verify --all -d backups")

        archive = sorted(n for n in os.listdir(os.path.join(demo.root, "backups"))
                         if n.endswith(".tar.gz"))[-1]
        with open(os.path.join(demo.root, "backups", archive), "r+b") as handle:
            handle.seek(900)
            handle.write(b"bit rot " * 4)
        tampered = demo.tool("verify --all -d backups")

        screenshot("backup-and-list.png", [backup, listing], "create a backup, then list")
        screenshot("dry-run.png", [dry_run], "see exactly what goes in before writing anything")
        screenshot("restore.png", [restore, restore_again, only],
                   "restore: verified, never overwrites silently")
        screenshot("recovery.png", [cleanup, broken, reindex, tampered],
                   "cleanup preview, damaged index recovery, corruption detection")
        animation("demo.gif", [
            ("# 1. Back up a project", []), backup,
            ("# 2. See what you have", []), listing,
            ("# 3. Prove the archive is intact", []), verify,
            ("# 4. Get everything back", []), restore,
        ], height_lines=17)
        del verify_all
    finally:
        demo.close()


if __name__ == "__main__":
    main()
