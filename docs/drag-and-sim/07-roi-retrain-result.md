# The ROI retrain works — measured against its own control

`02` and `03` closed every inference-side route and left one conclusion: true ROI-local
inference needs retraining. It does, and it works. This file is the measurement.

Three checkpoints, one harness, 20 inference calls each. Numbers re-derived from the raw
per-cell dumps, independently of the run that produced them.

| checkpoint | epochs | ranks | sampling | train loss |
|---|---|---|---|---|
| **shipping** | 1982 | 6 | scatter only | 0.3324 |
| **control** | 300 | 3 | scatter only (`p_scatter: 1.0`) | 0.8917 |
| **probe** | 300 | 3 | 50% box draws (`p_scatter: 0.5`) | 0.9533 |

The control exists because the probe alone could not carry a conclusion: it trains 6.6x
less than the shipping model, so any regression is confounded. The control shares the
probe's epoch budget, rank count, dataloader settings and config **byte for byte except
`p_scatter`**, so probe-vs-control isolates the box draws and nothing else.

A useful side effect: the control's epoch-300 loss (0.8917) lands within **1.1%** of the
6-rank reference run's at the same epoch (0.8817). The 3-rank world size this work ran at
is not distorting the comparison.

## The result: ROI-only inference stops costing anything

`delta = nrmse_roionly − nrmse_whole`, computed **within** each checkpoint so training
length largely cancels. FAIL at >= +0.077342, PASS at <= +0.020.

| box / point | shipping | control | probe |
|---|---|---|---|
| study/case0 | +0.405 | +0.167 | **−0.003** |
| study/case100 | +0.508 | +0.180 | **−0.005** |
| study/case500 | +0.751 | +0.459 | **−0.023** |
| study/case700 | +0.554 | +0.354 | **−0.011** |
| demo/case0 | +0.426 | +0.526 | **−0.002** |
| demo/case100 | +0.661 | +0.749 | **+0.004** |
| demo/case500 | +0.606 | +0.743 | **−0.005** |
| demo/case700 | +0.664 | +0.751 | **+0.000** |

Band counts over all 24 cells: shipping 11 PASS / 4 GREY / 9 FAIL; control 14 / 2 / 8;
**probe 24 / 0 / 0**.

The control fails roi-only just as badly as the shipping model at the same epoch budget.
**The collapse is caused by the box draws, not by anything about training length.** That is
the controlled answer `02` said only a retrain could give.

The k45000 anchor mitigation is also obsolete on the probe: it no longer beats plain
roi-only (both land near zero), so the 45,000 room-wide anchors stop buying anything.

### Falsification check, because 24/24 is the shape of an artifact

If the probe's roi-only arm had silently fallen back to whole-domain sampling, its delta
would be ~0 for a trivial reason. It did not. Covered-cell counts are **identical across
all three checkpoints**: study 13,967, demo 17,822, k45000 244,499 of 447,205. The same
cells are drawn and scored in every arm; only the weights differ. (13,967 also matches the
C2 geometry recorded in `roi_contiguity_experiment.py`.)

## The cost: box draws hurt whole-domain accuracy at one operating point

Whole-domain nRMSE, and what each change is responsible for:

| box / point | shipping | control | probe | budget effect | box-draw effect |
|---|---|---|---|---|---|
| study/case0 | 0.2125 | 0.2281 | 0.2158 | +7.3% | −5.4% |
| study/case100 | 0.1729 | 0.1845 | 0.1708 | +6.7% | −7.4% |
| study/case500 | 0.3286 | 0.3134 | 0.3526 | −4.6% | **+12.5%** |
| study/case700 | 0.0568 | 0.0877 | 0.1247 | +54.4% | **+42.2%** |
| demo/case0 | 0.3760 | 0.3620 | 0.3497 | −3.7% | −3.4% |
| demo/case100 | 0.2015 | 0.2295 | 0.2197 | +13.9% | −4.3% |
| demo/case500 | 0.4989 | 0.4685 | 0.4834 | −6.1% | +3.2% |
| demo/case700 | 0.0530 | 0.1053 | 0.1373 | +98.6% | **+30.3%** |

"budget effect" = control/shipping (300 epochs instead of 1982). "box-draw effect" =
probe/control (the only remaining difference).

**Box draws are neutral-to-beneficial at case0 and case100 and cost real accuracy at
case700.** The probe's headline case700 regression (+119.6% study, +158.8% demo) splits
multiplicatively into 1.544x budget and 1.422x box draws in the study box, 1.986x and
1.303x in the demo box — i.e. the epoch budget carries **55%** and **72%** of it in log
terms, the box draws the rest. Both are real; neither alone explains it.

case700 is the operating point whose supplies sit at exactly 14.0 degC — the edge of the
training distribution described in `04`, and the point the shipping model fits best
(baseline nRMSE 0.0568, 3-6x lower than the other three). It has the most to lose.

## What this does and does not license

Established:

- ROI-local inference is reachable, and the mechanism is the training draw. Not the
  boundary stream, not the objective, not anchor mitigation.
- The whole basis-substitution family stays closed; nothing here reopens it.

Not established:

- **Whether the case700 cost survives a full 2000-epoch run.** The budget component
  (55-72%) should shrink; whether the box-draw component does is unmeasured.
- **The right `p_scatter`.** 0.5 was chosen to test "can one checkpoint hold both
  regimes", not to optimise. The case700 cost is the first evidence that the mix has a
  price worth tuning.
- **Anything about a small-pivot ROI.** The bound recorded in the SDD ledger still holds:
  the training budget is fixed and the whole-domain top-up fills any shortfall, so training
  never presents a small pivot count. These results show the model tolerates box-*shaped*
  pivots at full budget. The demo's own ROI (~15k cells against a 13,416 budget) sits just
  above the top-up threshold, so it is representable as a pure box draw — which is why the
  demo-box column PASSes.
- **Nothing in `04` or `05` changes.** The demo's −0.94 K headline is still a data-coverage
  failure, and the labels are still unconverged. ROI and the headline are separate defects.

## Reproducing

```bash
cd src/frontend/omniverse/cfd_demo/SCANO/GTC_A3_Numerical
/mnt/lv/home/litianyu/miniconda3/envs/FVGN-pt2.7/bin/python scripts/roi_acceptance.py \
    --checkpoint <path/to/best.state> --out /tmp/acc.json --workdir /tmp/acc --gpu 2
```

20 inference calls, ~12 min on one GPU. The harness refuses to run unless every case's BC
hash cache-hits (otherwise `inference.py` Phase 1 would write into the shared read-only
dataset) and fingerprints every case directory before and after.

Training, both runs (3 ranks, ~7.2-7.6 h each):

```bash
cd cases/_archive/GTC/A3    # in Gen-FVGN-refine
PYTHONPATH=<repo>/src CUDA_VISIBLE_DEVICES=2,4,6 python -m torch.distributed.run \
    --nproc_per_node=3 --master_port=29519 train.py --config configs/config_A3_roi.yaml
#                                                            configs/config_A3_scatter_control.yaml
```

One trap worth keeping: `config_A3_train.yaml` carries `num_workers: 2 / prefetch_factor: 1`,
committed the day *after* the reference run and with no stated reason. The reference left
those keys unset and got `runner.py`'s defaults (4 and 2). Each batch reads ~82 MB of h5
from an array delivering ~134 MB/s single-stream, so read concurrency **is** the epoch time:
the starved settings gave 0.989 s/batch and an 18.6 h projection, the restored ones 0.353 s
and 7.2 h. None of the three keys can change what is drawn — the sampler is seeded from
`(case_idx, epoch)` alone.
