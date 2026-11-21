from pathlib import Path
import sys
F=Path('/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/ov-output-layout-owner-private-20261007/FROZEN-v1')
sys.argv=[str(F/'REPRO.py'),'--output','/mnt/lv/home/litianyu/mycode/dcdtuni/.codex-tmp/ov-output-layout-owner-independent-review-shared-20261007/fresh','--cpu','36']
s=Path(__file__).with_name('REPRO.cpu36.py').read_text()
exec(compile(s,str(F/'REPRO.py'),'exec'),{'__name__':'__main__','__file__':str(F/'REPRO.py')})
