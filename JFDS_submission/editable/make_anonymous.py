"""Write a review copy of the manuscript source that contains no author details.

The single source keeps author material inside \\ifblind ... \\else ... \\fi
blocks. Here every such block is resolved to its blind branch and the other
branch is deleted, so the names are not even present in the .tex file.
"""
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
OUT = Path(sys.argv[2])

HEADER = r"""%==============================================================================
%  The Journal of Finance and Data Science -- Research article (elsarticle)
%  Anonymized manuscript for double-anonymized review: no author details.
%  Compile with pdfLaTeX, three runs. The bibliography is inline (no BibTeX).
%=============================================================================="""

IDENTIFIERS = ["kancharla", "panthangi", "ratnachand", "srinivas", "nmims",
               "malla reddy", "gmail", "0009-0003-5634-8209", "0009-0009-2363-4667",
               "github.com/ratnachand04"]


def main():
    lines = SRC.read_text(encoding="utf-8").split("\n")
    # drop the original header comment block (everything before \documentclass)
    start = next(i for i, l in enumerate(lines) if l.startswith(r"\documentclass"))
    lines = HEADER.split("\n") + lines[start:]
    out, mode = [], None
    for line in lines:
        s = line.strip()
        if s == r"\newif\ifblind" or s.startswith(r"\blindfalse") or s.startswith(r"\blindtrue"):
            continue
        if s == r"\usepackage{orcidlink}":
            continue
        if s == r"\ifblind":
            assert mode is None, "nested \\ifblind"
            mode = "keep"
            continue
        if mode is not None and s == r"\else":
            mode = "drop"
            continue
        if mode is not None and s == r"\fi":
            mode = None
            continue
        if mode == "drop":
            continue
        out.append(line)
    assert mode is None, "unterminated \\ifblind"
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    low = text.lower()
    found = [w for w in IDENTIFIERS if w in low]
    assert not found, f"identifiers left in anonymous source: {found}"
    assert r"\ifblind" not in text
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT} ({len(text.splitlines())} lines); identifier check passed")


if __name__ == "__main__":
    main()
