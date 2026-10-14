"""Build installable LocalForge packages with the browser UI bundled in.

Usage (from the repository root):

    python scripts/build_release.py              # npm ci, build the UI, build the packages
    python scripts/build_release.py --skip-install

The wheel and source archive land in backend/dist/. Install one locally with
`uvx --from backend/dist/<wheel> localforge`, or publish them with `uv publish`.
"""

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
UI_BUILD = FRONTEND / "dist"
BUNDLED_UI = BACKEND / "src" / "localforge" / "web"
PACKAGES = BACKEND / "dist"


def run(command: list[str], cwd: Path) -> None:
    executable = shutil.which(command[0])
    if executable is None:
        sys.exit(f"build_release: '{command[0]}' was not found on PATH.")
    print(f"\n$ {' '.join(command)}  (in {cwd.relative_to(ROOT) or '.'})", flush=True)
    subprocess.run([executable, *command[1:]], cwd=cwd, check=True)


def build_ui(skip_install: bool) -> None:
    if not skip_install:
        run(["npm", "ci"], FRONTEND)
    run(["npm", "run", "build"], FRONTEND)
    if not (UI_BUILD / "index.html").is_file():
        sys.exit("build_release: the UI build did not produce frontend/dist/index.html.")
    if BUNDLED_UI.exists():
        shutil.rmtree(BUNDLED_UI)
    shutil.copytree(UI_BUILD, BUNDLED_UI)
    print(f"Bundled the UI into {BUNDLED_UI.relative_to(ROOT)}")


def build_packages() -> list[Path]:
    if PACKAGES.exists():
        shutil.rmtree(PACKAGES)
    run(["uv", "build", "--out-dir", str(PACKAGES)], BACKEND)
    return sorted(PACKAGES.iterdir())


def check_wheel(artifacts: list[Path]) -> None:
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    if len(wheels) != 1:
        sys.exit(f"build_release: expected one wheel, found {[path.name for path in wheels]}.")
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    if "localforge/web/index.html" not in names:
        sys.exit("build_release: the wheel is missing the bundled UI (localforge/web/index.html).")
    if not any(name.startswith("localforge/web/assets/") for name in names):
        sys.exit("build_release: the wheel is missing the bundled UI assets.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--skip-install", action="store_true", help="Reuse frontend/node_modules (skip npm ci)."
    )
    args = parser.parse_args()
    build_ui(args.skip_install)
    artifacts = build_packages()
    check_wheel(artifacts)
    print("\nBuilt:")
    for artifact in artifacts:
        print(f"  {artifact.relative_to(ROOT)}  ({artifact.stat().st_size / 1024:,.0f} KiB)")


if __name__ == "__main__":
    main()
