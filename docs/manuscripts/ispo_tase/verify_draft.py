from pathlib import Path
import re, json, subprocess, hashlib
p = Path(__file__).resolve().parent
repo = p.parents[2]
main = (p / "main.tex").read_text()
abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", main, re.S).group(1)
ntp = main.split(r"\textbf{Note to Practitioners---}", 1)[1].split(r"\begin{IEEEkeywords}", 1)[0]
def words(s):
    s = re.sub(r"\\[A-Za-z]+(?:\[[^\]]*\])?", " ", s)
    return len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*", s))
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
counts = dict(abstract_words=words(abstract), ntp_words=words(ntp))
assert counts["abstract_words"] <= 200, counts
assert 100 <= counts["ntp_words"] <= 300, counts
assert r"\documentclass[journal,10pt]{IEEEtran}" in main
for name in ("main", "supplementary"):
    log = (p / f"build/{name}.log").read_text(errors="replace")
    for bad in ("Undefined control sequence", "LaTeX Error:",
                "There were undefined references", "Overfull \\hbox"):
        assert bad not in log, (name, bad)
    assert not re.search(r"(?im)^.*(?:citation|reference).*undefined.*$", log), name
    counts[f"{name}_pdf_info"] = subprocess.check_output(
        ["pdfinfo", str(p / f"build/{name}.pdf")], text=True)
assert r"\pending{" in (p / "sections/results.tex").read_text(), (
    "Current incomplete study scope must stay explicit; revise this guard only "
    "after the full journal completion audit.")
generators = {
    "e5_structured_terminal": "src/agent/analysis/complete_outer_cost_figures_v6b.py",
    "e5_architecture_frontier": "src/agent/analysis/complete_outer_cost_figures_v6b.py",
    "mfpg_original_cohort": "src/agent/analysis/official_mfpg_cohort_figure_v13.py",
}
checked = []
for name in ("controlled_fixed_updates", *generators):
    provenance = json.loads((p / f"figures/{name}.provenance.json").read_text())
    expected = provenance.get("figure_sha256", provenance.get("PDF_sha256"))
    assert sha(p / f"figures/{name}.pdf") == expected, name
    if "PNG_sha256" in provenance:
        assert sha(p / f"figures/{name}.png") == provenance["PNG_sha256"], name
    bindings = dict(provenance.get("source_artifacts", {}))
    for key in ("analysis_manifest", "summary", "published_reference"):
        if key in provenance:
            bindings[provenance[key]] = provenance[key + "_sha256"]
    for path, expected_sha in bindings.items():
        assert sha(repo / path) == expected_sha, path
    if name in generators:
        assert sha(repo / generators[name]) == provenance["source_sha256"], name
    if name == "controlled_fixed_updates":
        assert sha(repo / provenance["figure_generator"]) == provenance["source_sha256"], name
    if name == "controlled_fixed_updates":
        assert provenance["equal_wall_claim"] is False
    checked.append({"figure": name, "primary_bindings": len(bindings)})
provenance = json.loads((p / "figures/shinaide_six_family_plans.provenance.json").read_text())
assert sha(p / "figures/shinaide_six_family_plans.pdf") == provenance["PDF_sha256"]
assert sha(p / "figures/shinaide_six_family_plans.png") == provenance["PNG_sha256"]
assert sha(Path(provenance["figure_source"])) == provenance["figure_source_sha256"]
for path, expected_sha in provenance["source_artifacts_sha256"].items():
    assert sha(repo / path) == expected_sha, path
assert len(provenance["projects"]) == 6
for project in provenance["projects"]:
    assert sha(Path(project["project_path"])) == project["project_sha256"]
checked.append({"figure": "shinaide_six_family_plans", "primary_bindings": len(provenance["source_artifacts_sha256"]), "projects": 6})
counts["source_bound_figures"] = checked
primary_results = json.loads((p / "RESULTS_PROVENANCE.json").read_text())
assert primary_results["full_journal_complete"] is False
for claim, record in primary_results["claims"].items():
    assert sha(repo / record["path"]) == record["sha256"], claim
counts["source_bound_text_results"] = list(primary_results["claims"])
print(json.dumps(counts, indent=2))
