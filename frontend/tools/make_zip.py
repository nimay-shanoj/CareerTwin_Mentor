"""Package the project into a zip file.

The archive contains the whole project folder (`Career-Twin-main/…`) so that
unzipping it gives a ready-to-run directory.

Excluded on purpose:
  .venv/          the virtualenv (rebuilt with pip install -r requirements.txt)
  .git/           version control history
  instance/       the local database with real data
  .env            contains the secret key - .env.example is included instead
  __pycache__/    compiled Python
  *.log           runtime logs
  *.zip           archives
"""

import os
import sys
import zipfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../frontend
REPO = os.path.dirname(APP)                                       # .../Career-Twin-main
BASE = os.path.dirname(REPO)                                      # the folder that holds the project
OUT = os.path.join(BASE, "Career-Twin-revamp.zip")

EXCLUDE_DIRS = {
    ".venv", "venv", "env", ".git", "__pycache__", ".pytest_cache", "instance",
    ".mypy_cache", "node_modules", ".idea", ".vscode",
}
EXCLUDE_FILES = {".env", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIX = (".zip", ".log", ".err", ".out", ".pyc", ".pyo", ".db", ".sqlite")


def should_skip(name):
    if name in EXCLUDE_DIRS or name in EXCLUDE_FILES:
        return True
    return name.endswith(EXCLUDE_SUFFIX)


def main():
    if os.path.exists(OUT):
        os.remove(OUT)

    count = 0
    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for current, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if not should_skip(d)]
            for name in files:
                if should_skip(name):
                    continue
                path = os.path.join(current, name)
                archive.write(path, os.path.relpath(path, BASE))
                count += 1

    with zipfile.ZipFile(OUT) as archive:
        infos = archive.infolist()
        total = sum(i.file_size for i in infos)
        names = [i.filename for i in infos]
        folders = sorted({os.path.dirname(n) for n in names})

    size = os.path.getsize(OUT)
    print(f"Created {OUT}")
    print(f"  {count} files, {size / 1024:.0f} KB compressed ({total / 1024 / 1024:.1f} MB raw)")
    print(f"  {len(names)} entries in {len(folders)} folders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
