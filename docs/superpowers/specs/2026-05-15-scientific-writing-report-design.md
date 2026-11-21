# Scientific Writing Training Report — Design Spec

**Date:** 2026-05-15
**Author:** Tianyu Li
**Status:** Approved

---

## Goal

Write a reflective report on a 2.5-day Scientific Writing workshop (4–6 May 2026, Justin Lebrun, Scientific Reach). The report uses the author's own DCTwin multi-agent CFD paper as a real case study, demonstrating before/after revisions for three key writing concepts: word spikes, word meandering, and fluidity.

## Constraints

- **Format:** IEEEtran journal LaTeX (template: `docs/scientificWriting/bare_jrnl.tex`)
- **Language:** English
- **Length:** ~4–5 pages (IEEE double-column)
- **Audience:** Supervisor / university (formal academic report)
- **Case study source:** `docs/manuscript/paper_build/gen_docxml.py` revision history (commit `ed9c863` → `79cbdc0` → current)
- **Visual style:** Use existing `\revdel{}` (strikethrough) and `\revadd{}` (blue) commands for before/after

## Output File

`docs/scientificWriting/report.tex` (new file, based on `bare_jrnl.tex` template)

## Title

*"From Word Spikes to Fluidity: Lessons from a Scientific Writing Workshop Applied to a Multi-Agent CFD Paper"*

---

## Section Structure

### I. Introduction (~0.5 page)

- Workshop context: 4–6 May 2026, 2.5-day intensive, led by Justin Lebrun (Scientific Reach), co-author of *Scientific Writing 3.0*
- Author's situation: non-native English-speaking PhD student, simultaneously writing a multi-agent data center CFD paper for AIFLUIDs 2026
- Report purpose: summarise the three most impactful concepts learned, demonstrate them through real before/after revisions from the author's own paper

### II. Three Principles of Reader-Centric Writing (~1.5 pages)

Three subsections, each with: definition → cognitive/reader-experience explanation → short illustrative example.

**II-A. Word Spikes**
- Definition: unnecessarily complex vocabulary that creates a sudden jump in reading difficulty
- Why it's a trap for non-native writers (thesaurus overuse, perceived formality)
- Reader-experience: forces a decoding pause → "reading accident" (Lebrun's term)

**II-B. Word Meandering (Delayed Main Verb)**
- Definition: subject separated from main verb by long intervening material
- Working memory limitation (Miller's 7±2) — reader loses the subject
- Structural pattern diagram: `Subject ... [modifiers] ... verb`

**II-C. Fluidity**
- Definition: smooth progression of ideas via given-new contract
- Three levels: sentence-to-sentence, paragraph-to-paragraph, section-to-section
- Common fluidity breakers: parenthetical figure refs, orphan sections, abrupt topic shifts

### III. Case Study: Revising a Multi-Agent CFD Paper (~2 pages)

One-sentence paper context, then three subsections with real before/after examples.

**III-A. Word Spike Diagnosis** — 2–3 examples using `\revdel{}`/`\revadd{}`
- "extraordinarily manual" → "highly manual"
- "Critically, many common errors" → "Errors such as"
- Analysis: why the simpler form is better

**III-B. Meandering Sentence Repair** — 2–3 examples
- 76-word chained-verb sentence → 25-word three-action sentence
- Numbered contribution mega-sentence (C1/C2/C3) → split into natural paragraphs
- Word count comparison

**III-C. Fluidity Improvement** — 2–3 examples
- "(Figure 1)" → "as depicted in Figure 1"
- Orphan section heading removed
- Introduction opening: background-first → problem-first
- Optional: summary statistics table (avg sentence length before/after, spike count reduction)

### IV. Reflections and Takeaways (~0.5 page)

- Shift from writer-centric to reader-centric mindset
- Practical tools: Fluiditext web app, self-review checklist
- Action plan for future papers

### V. Conclusion (~3–4 sentences)

### References

- Lebrun & Lebrun (2021). Scientific Writing 3.0.
- PMC articles on fluidity, conciseness, logical flow
- Workshop materials (if citable)

---

## Reference Materials

All saved to `docs/scientificWriting/Ref/`:
- `instructor_and_book.md` — Justin Lebrun bio, book details, seminar topics
- `three_concepts.md` — Definitions, cognitive science, literature links
- `dctwin_before_after_examples.md` — Real before/after examples from DCTwin paper with analysis

## Implementation Notes

- Start from `bare_jrnl.tex` template, strip the PIG-NODE content, keep preamble packages
- `\revdel{}` and `\revadd{}` commands already defined in template — use directly
- BibTeX entries go in `bibtex/` directory
- Figures (if any TikZ charts) go in `Figures/` directory
