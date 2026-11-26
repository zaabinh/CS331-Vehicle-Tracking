"""Initialize the pinned upstream clone and apply documented compatibility fixes."""

import subprocess
from pathlib import Path


def main():
    """Leave existing work intact when a patch cannot be applied cleanly."""
    root = Path(__file__).resolve().parents[1]
    upstream = root / "UCMC/UCMCTrack"
    patch = root / "patches/ucmc_compat.patch"
    if not (upstream / "tracker/ucmc.py").is_file():
        subprocess.run(
            ["git", "submodule", "update", "--init", "--recursive"],
            cwd=root,
            check=True,
        )
    reverse = subprocess.run(
        ["git", "apply", "--reverse", "--check", str(patch)],
        cwd=upstream,
        capture_output=True,
    )
    if reverse.returncode == 0:
        print("UCMC compatibility patch is already applied.")
        return
    subprocess.run(
        ["git", "apply", "--check", str(patch)], cwd=upstream, check=True
    )
    subprocess.run(["git", "apply", str(patch)], cwd=upstream, check=True)
    print("UCMC compatibility patch applied.")


if __name__ == "__main__":
    main()
