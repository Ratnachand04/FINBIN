"""Create a source-only Overleaf import ZIP without output-PDF name collisions."""
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "paper/jfds_overleaf"
    output = ROOT / "deliverables/JFDS_Overleaf_Import.zip"
    paths = ["main.tex", "title_page.tex", "references.bib", "highlights.txt",
             "START_HERE.txt", "figures/evidence.pdf"]
    paths.extend(p.relative_to(source).as_posix() for p in (source / "generated").glob("*.tex"))
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(paths):
            archive.write(source / name, name)
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert "main.pdf" not in archive.namelist()
        assert "title_page.pdf" not in archive.namelist()
        for name in paths:
            assert archive.read(name) == (source / name).read_bytes()
    print(f"Verified: {output}")
    print(f"Files: {len(paths)}; bytes: {output.stat().st_size}")
    print(f"SHA256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
