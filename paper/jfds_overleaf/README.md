# Overleaf project: author-review research draft

1. In Overleaf choose **New Project > Upload Project** and select `JFDS_Overleaf.zip`.
2. Set `main.tex` as the main document. Choose pdfLaTeX (with automatic BibTeX), or XeLaTeX.
3. The ZIP includes the bibliography, generated result tables, and vector figure; no external file paths are needed.
4. Compile `title_page.tex` separately after supplying the actual author information.

The provided `main.pdf` was checked using Tectonic/XeTeX. Overleaf itself was not accessed. The source uses standard `elsarticle` and packages available in normal Overleaf TeX installations.

## What this paper evaluates

A 30-feature daily cryptocurrency classifier and an auditable evaluation workflow, not the entire repository's LLM/FinBERT/Prophet/live-trading platform. The results were recomputed from the supplied files. Unfavorable and inconclusive results are retained. No results, authors, funding, or approvals were invented.

This project supersedes the supplied `crypto_direction_evaluation.tex` and the original PDF for the new research draft. Those earlier files are preserved in the workspace for comparison; their historical economic tables and strong conclusions should not be mixed with this version.

## Before submission

- Complete author names/order, affiliations, corresponding email/address, CRediT roles, funding and interests in `title_page.tex` and the applicable manuscript declarations.
- Independently review and approve the methods, code, numerical outputs, references and prose. Finalize the AI disclosure truthfully. The journal places restrictions on AI-assisted writing and requires human oversight; the current AI-assisted draft is not itself evidence that these requirements have been met.
- Confirm originality, all-author approval, prior-publication status and that the paper is not simultaneously under review elsewhere.
- Confirm input-data and code redistribution rights. Review the replication archive for identities and metadata; no public deposit or DOI has been created. Saved joblib model files may contain local build paths in binary metadata and should be regenerated in a neutral directory or omitted for double-anonymized review.
- Complete the publisher's competing-interests declaration tool and upload its required Word document. Do not substitute invented declarations.
- Review journal fit and novelty. This is a single-artefact evaluation case study with limited predictive evidence, not a new model architecture or a validated profitable strategy. Acceptance cannot be promised.
- A fresh prospective holdout, alternate exchange/bar-boundary data, realistic shorting costs, and paper trading would strengthen a future extension. They have not been represented as completed experiments here.

The journal is **The Journal of Finance and Data Science**, published by KeAi with Elsevier services, not the differently titled *The Journal of Financial Data Science*. The [official author guide](https://www.keaipublishing.com/en/journals/the-journal-of-finance-and-data-science/guide-for-authors/) was checked on 22 September 2026. Follow its current double-anonymized review, editable-source, declaration and AI requirements; recheck before submitting. No submission has been made.

## Numerical provenance

All `generated/*.tex` numeric tables and macros come from the research scripts in the companion replication archive. Do not hand-edit a number in one table without rebuilding all derived outputs. The primary estimates include final target liquidation; consequently a few values differ slightly from the supplied draft even where the predictions are identical.

The replication package is separate because it contains training data, Python source and binary models that are not needed to compile the manuscript. Run `python scripts/reproduce_jfds.py` in that package after installing `requirements-research.txt`. Details and known limitations are in its `RESEARCH_README.md`.
