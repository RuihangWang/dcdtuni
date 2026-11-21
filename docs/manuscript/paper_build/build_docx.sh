set -e
cd /mnt/lv/litianyu/mycode/dcdtuni/docs/manuscript/paper_build
python3 gen_docxml.py
# Ensure the packdoc validation dependency is present in whatever env is active
python3 -c "import docx" 2>/dev/null || python3 -m pip install -q python-docx
python3 packdoc.py
