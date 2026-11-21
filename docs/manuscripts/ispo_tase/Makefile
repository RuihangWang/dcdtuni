ROOT := $(abspath ../../..)
DRAFT_TMP := $(ROOT)/_tmp/ispo_tase
export TMPDIR := $(DRAFT_TMP)/tmp
export TEXMFVAR := $(DRAFT_TMP)/texmf-var
export TEXMFCONFIG := $(DRAFT_TMP)/texmf-config

.PHONY: all verify render
all:
	mkdir -p build $(TMPDIR) $(TEXMFVAR) $(TEXMFCONFIG)
	latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build main.tex
verify: all supplementary
	python3 verify_draft.py
render: all
	mkdir -p $(DRAFT_TMP)/render
	pdftoppm -r 110 -png build/main.pdf $(DRAFT_TMP)/render/page

.PHONY: supplementary
supplementary:
	mkdir -p build $(TMPDIR) $(TEXMFVAR) $(TEXMFCONFIG)
	latexmk -pdf -interaction=nonstopmode -halt-on-error -outdir=build supplementary.tex
