import os, zipfile, sys
root, out = "unpacked", "paper_final.docx"
if not os.path.isdir(root):
    print("NO unpacked/ dir"); sys.exit(1)
skip_top, skip_names = {"paper.md"}, {".DS_Store"}
files = []
for dp, dn, fn in os.walk(root):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for f in fn:
        if f in skip_names: continue
        full = os.path.join(dp, f); rel = os.path.relpath(full, root)
        if rel in skip_top: continue
        files.append((full, rel))
def key(it):
    rel = it[1]
    return (0, rel) if rel == "[Content_Types].xml" else (1, rel) if rel == "_rels/.rels" else (2, rel)
files.sort(key=key)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for full, rel in files: z.write(full, rel.replace(os.sep, "/"))
print("Packed %d parts -> %s (%d bytes)" % (len(files), out, os.path.getsize(out)))
zf = zipfile.ZipFile(out)
doc = zf.read("word/document.xml").decode("utf-8", "replace")
print("testzip:", zf.testzip(), "| has_Acknowledgements:", "Acknowledgements" in doc)
try:
    import docx
    print("python-docx opened OK; paragraphs:", len(docx.Document(out).paragraphs))
except Exception as e:
    print("python-docx check skipped:", repr(e))
