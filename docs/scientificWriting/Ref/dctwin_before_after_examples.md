# Before/After Examples from DCTwin Paper Revision

Source: gen_docxml.py revisions (commit ed9c863 → 79cbdc0 → current)

---

## Word Spike Examples

### Example 1: "extraordinarily"
- **Before:** "preparing an OpenFOAM case for a realistic data center layout remains **extraordinarily** manual and error-prone"
- **After:** "preparing a CFD case with a numerical solver such as OpenFOAM remains **highly** manual"
- **Analysis:** "extraordinarily" is a 7-syllable word spike; "highly" conveys the same emphasis without forcing a pause.

### Example 2: "Critically" as sentence opener
- **Before:** "**Critically**, many common errors — a misoriented rack, a mismatched power value, or a missing containment baffle — do not crash the solver"
- **After:** "Errors such as a misoriented rack or a mismatched power value do not crash the solver"
- **Analysis:** "Critically" is an unnecessary evaluative word spike. The sentence's content already conveys importance; the adverb is redundant.

### Example 3: "corroborate" (hypothetical — typical in early drafts)
- General pattern: using "utilize" where "use" suffices, "elucidate" where "explain" works, "endeavour" where "try" is clearer.

---

## Word Meandering Examples

### Example 1: 76-word monster sentence
- **Before:** "An engineer must translate CAD drawings into watertight STL geometry with hundreds of named surface regions, extract per-server power and airflow data from equipment schedules and spreadsheets, define boundary conditions for every rack inlet and outlet, cooling unit face, perforated tile, and baffle, configure mesh refinement to resolve thin gaps between closely spaced equipment, and assemble consistent solver dictionaries across dozens of interdependent files."
- **After:** "an engineer must produce watertight STereoLithography (STL) geometry, assign boundary conditions for every rack and cooling unit, and assemble consistent solver dictionaries."
- **Analysis:** The original sentence has ONE subject ("An engineer") and FIVE chained verb phrases spanning 76 words. The reader must hold "An engineer must" in memory across an enormous verb chain. The revision keeps only the three essential actions (produce, assign, assemble), reducing to 25 words.

### Example 2: Contribution sentence with interleaved C1/C2/C3
- **Before:** "This paper presents a multi-agent framework for automated data center CFD case generation with three contributions: (C1) a Builder–Evaluator–Judge multi-agent architecture that enables iterative, self-correcting geometry generation through a closed-loop verification cycle (Figure 1); (C2) a parametric geometry modeling system exposed through a Playwright-based headless browser API, allowing AI agents to programmatically operate the same professional-grade Three.js modeling environment used by human engineers; and (C3) a sketch-to-simulation workflow that converts 2D layout pictures..."
- **After:** "This paper presents a multi-agent framework for automated data center CFD case generation. The framework introduces an Orchestrator–Builder–Evaluator architecture that enables iterative, self-correcting geometry generation through a closed-loop verification cycle. A Playwright-based headless browser API bridges LLM text generation and complex 3D geometry construction..."
- **Analysis:** The original packs three complex contributions into a single sentence with numbered labels. The reader must parse (C1), (C2), (C3) while tracking the overall grammar. The revision splits into natural sentences, each making one point.

---

## Fluidity Examples

### Example 1: Parenthetical figure references → natural phrases
- **Before:** "...through a closed-loop verification cycle **(Figure 1)**"
- **After:** "...through a closed-loop verification cycle, **as depicted in Figure 1**"
- **Analysis:** Parenthetical "(Figure 1)" is a reading accident — the reader must briefly leave the sentence flow to register the reference. Natural integration ("as depicted in Figure 1") maintains the reading flow.

### Example 2: Orphan section heading
- **Before:** 
  ```
  2. Method
  2.1 Pipeline Overview
  [Figure 1 caption]
  ```
- **After:**
  ```
  2. Method
  [Figure 1 caption]
  As illustrated in Figure 1, the Orchestrator drives the entire pipeline...
  2.1 Multi-Agent Architecture
  ```
- **Analysis:** A section heading immediately followed by a subsection heading creates an "orphan" — the reader expects introductory text under "2. Method" but gets nothing. The fix adds a paragraph that bridges from the section heading to the first subsection.

### Example 3: Introduction opening — background-first vs problem-first
- **Before:** "Data centers consume an estimated 1–2% of global electricity, with projections of substantial growth driven by cloud computing and large-scale AI training."
- **After:** "Thermal modeling via Computational Fluid Dynamics (CFD) is widely used to predict hotspots and optimize cooling in data centers."
- **Analysis:** The old opening starts with broad context (electricity consumption statistics) that is not directly relevant to the paper's topic. The reader must navigate through background before understanding what the paper is about. The revised opening directly enters the paper's domain (CFD thermal modeling), establishing the topic in the first sentence.

### Example 4: Abstract restructured for flow
- **Before:** "We present a multi-agent framework... The framework features: (1)...; (2)...; (3)..."
- **After:** "This paper presents a multi-agent framework that automates this pipeline, converting... The framework combines an Orchestrator–Builder–Evaluator architecture..."
- **Analysis:** The original abstract uses a numbered list embedded in prose, which disrupts flow. The revision weaves contributions naturally into the narrative.
