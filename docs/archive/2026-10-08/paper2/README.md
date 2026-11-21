# paper2 (Differentiable Design Languages) — IEEE TNNLS draft

> **ABANDONED FOR THE NEW JOURNAL PAPER — 2026-09-28.** The author has
> withdrawn this research route and its reported results as evidence for the
> new liquid DAE design study. Keep this draft and its source records for
> provenance only. Its numbers, labels, regret and physical model must not be
> carried into the new manuscript. This status does not change the accepted
> AIFLUIDs conference paper.

Builds (pdflatex x2 + bibtex per jobname):
- `main.tex` — open build (authors, funding, biographies).
- `main_blind.tex` — wrapper `\def\BLINDREVIEW{}\input{main.tex}` (double-anonymous).
- `supplementary.tex` / `supplementary_blind.tex` — same wrapper pattern.

Page budget (round 21, 2026-09-16): main open 15 pp (TNNLS ceiling, references and
biographies included), main blind 14 pp, supplementary open 14 pp, blind 13 pp.
The supplementary is a separate document and sits outside the ceiling.

Double-anonymous handling: authors, affiliations, funding, acknowledgment and
biographies are suppressed in blind builds; the two self-citations
(`li2026dctwin`, `phythesis2025`) render as `Anonymous` through
`references_blind.bib`; the artefact map is open-only; reference numbering is
identical in both builds ([1] is the conference version in each).

Main-text figures: Fig. 1 pipeline schematic; Fig. 2 Benchmark-B geometry
layout, three designs side by side; Fig. 3 solved mass-flow and temperature
fields of the same three designs.  The five-method geometry gallery plate is
Supplementary Fig. S11; further plates S5-S10 sit in Supplementary Sec. S-IX
and are cited by the main text.

Supplementary sections S-I to S-XI.  S-IX holds the floats the main text cites
by supplementary number so that the main text stays within its page ceiling.

House rules: sentences of at most 35 words; no parenthetical "(Fig. n)"; every
numeric claim carries a `% source:` comment naming the case logger directory
and JSON key; captions are self-contained; figure captions end with a terminal
period, table captions carry none.

Figures are produced by `ddl/tools/make_figures.py` (Type-42 fonts, at least
7 pt at placed size); the pipeline schematic by
`ddl/tools/make_fig_pipeline.py`.

Status 2026-09-16, round 21: format-referee fixes landed (prose released from
the Fig. 2 float, blind hygiene, dangling cross-references, IEEE reference
style, caption periods, front-matter footnotes).  Science integration still
running on the GPU server: J-decomposition v2, Benchmark-B five-seed
post-training table, and the 27B scale run.
