LATEXMK ?= latexmk
export TEXINPUTS := ./vendor/icml2026//:$(TEXINPUTS)
export BSTINPUTS := ./vendor/icml2026//:$(BSTINPUTS)

.PHONY: all check ship clean archive snapshot splice
all:
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error -file-line-error -outdir=build main.tex

# The tracked main.pdf is the artifact a reader downloads, and nothing produced it: a review
# found it three pages and two E11e tasks behind build/main.pdf, the one every gate reads.
# `ship` copies, and check_manuscript.py's shipped-PDF gate then fails until it has been run,
# so the gate has teeth rather than being satisfied by `check` depending on `ship`.
# The archive section in main.tex is generated.  Regenerate and splice it in one step,
# because a generated section that is hand-edited is the defect check_measurement_archive.py
# exists to catch.
snapshot archive:
	python3 scripts/snapshot_records.py

archive: splice
splice:
	python3 scripts/splice_archive.py

ship: all
	cp build/main.pdf main.pdf

check: all
	python3 scripts/check_manuscript.py

# Keep generated PDFs for review; remove only reproducible auxiliary files.
clean:
	$(LATEXMK) -c -outdir=build main.tex
