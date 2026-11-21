"""
src/Precompute/stage_embeddings.py
===================================
Precompute MiniLM text embeddings for all cases and write them back into
each case's H5 file under ``text_conditioning/<output_dataset_name>``.

Pipeline per H5 file
--------------------
1. Read ``text_conditioning/patch_unique_input_ids``  (P_unique, 192)
   and  ``text_conditioning/patch_unique_attention_mask`` (P_unique, 192).
2. Run ``QwenTextConditionEncoder.forward`` → unique embeddings (P_unique, 384).
3. Expand via ``text_conditioning/patch_text_inverse_indices`` (P,)
   → per-patch embeddings (P, 384).
4. Write back via ``write_embeddings_to_h5``.
"""

from __future__ import annotations

import logging
from typing import List

import h5py
import numpy as np
import torch

from .h5_layout import write_embeddings_to_h5

logger = logging.getLogger(__name__)

# H5 dataset paths inside the ``text_conditioning/`` group
_TC_GROUP = "text_conditioning"
_DS_INPUT_IDS = "patch_unique_input_ids"
_DS_ATTN_MASK = "patch_unique_attention_mask"
_DS_INVERSE_IDX = "patch_text_inverse_indices"


def precompute_embeddings(
    h5_paths: List[str],
    encoder_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    random_init: bool = False,
    random_seed: int = 42,
    output_dataset_name: str = "pretrained_patch_embeddings",
    device: str = "cuda",
    force: bool = False,
    micro_batch_size: int = 256,
) -> dict:
    """Precompute and store text embeddings for every H5 file in *h5_paths*.

    For each file the function:
      1. Checks idempotency — skips if the output dataset already exists and
         ``force`` is False.
      2. Reads the pre-tokenized unique input IDs and attention masks.
      3. Runs the encoder (with ``torch.no_grad`` + ``bfloat16`` autocast).
      4. Expands unique embeddings to per-patch embeddings via inverse indices.
      5. Writes the result back to the H5 file.

    Parameters
    ----------
    h5_paths:
        List of absolute paths to H5 files to process.
    encoder_name:
        HuggingFace model name / path for the text encoder backbone.
    random_init:
        If True, initialise the encoder from random weights instead of
        pretrained weights (useful for ablations / unit tests without
        network access).  ``torch.manual_seed(random_seed)`` is called before
        the encoder is constructed to ensure reproducibility.
    random_seed:
        Seed used when ``random_init=True``.
    output_dataset_name:
        Name of the dataset written inside ``text_conditioning/`` in each H5
        file (e.g. ``"pretrained_patch_embeddings"``).
    device:
        Torch device string to run the encoder on (``"cuda"`` or ``"cpu"``).
    force:
        When True, overwrite an already-existing output dataset.
    micro_batch_size:
        Number of token sequences to feed to the encoder at once.  The encoder
        itself also has an internal micro-batch loop; this parameter is passed
        to its constructor as ``encode_micro_batch_size``.

    Returns
    -------
    dict
        ``{"processed": int, "skipped": int}`` — counts of files that were
        processed vs skipped.
    """
    processed = 0
    skipped = 0

    # The encoder is built lazily — only when the first file that actually
    # needs processing is encountered.  This avoids downloading HuggingFace
    # weights (or touching the GPU) when all files are already up-to-date.
    encoder = None

    for h5_path in h5_paths:
        # Fast idempotency check before touching the encoder
        if not _needs_processing(h5_path, output_dataset_name, force):
            logger.debug(
                "stage_embeddings: text_conditioning/%s already exists in %s — skipping",
                output_dataset_name,
                h5_path,
            )
            skipped += 1
            continue

        # Lazy encoder construction — done at most once
        if encoder is None:
            if random_init:
                torch.manual_seed(random_seed)

            # Import here to avoid pulling in heavy HuggingFace deps at module
            # import time when only the function signature is needed.
            from src.NNmodels.TransFVGN.TextCondTransFVGN_v1 import (
                QwenTextConditionEncoder,
            )

            encoder = QwenTextConditionEncoder(
                model_name=encoder_name,
                lora_rank=16,
                lora_alpha=32,
                lora_dropout=0.05,
                use_gradient_checkpointing=False,  # no grads for inference
                encode_micro_batch_size=micro_batch_size,
                random_init=random_init,
            )
            encoder.to(device)
            encoder.eval()
            logger.info("stage_embeddings: encoder built on device=%s", device)

        result = _process_one(
            h5_path=h5_path,
            encoder=encoder,
            output_dataset_name=output_dataset_name,
            device=device,
            force=force,
        )
        if result:
            processed += 1
        else:
            # _process_one returning False here means the file had a structural
            # problem (missing group/datasets) — count as skipped.
            skipped += 1

    logger.info(
        "precompute_embeddings: finished — processed=%d, skipped=%d",
        processed,
        skipped,
    )
    return {"processed": processed, "skipped": skipped}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _needs_processing(h5_path: str, output_dataset_name: str, force: bool) -> bool:
    """Return True if *h5_path* needs embedding computation.

    Returns False (skip) when the output dataset already exists and force is
    False, or when the file cannot be opened at all.
    """
    try:
        with h5py.File(h5_path, "r") as f:
            already_exists = (
                _TC_GROUP in f
                and output_dataset_name in f[_TC_GROUP]
            )
        if already_exists and not force:
            return False
        return True
    except Exception as exc:
        logger.warning("stage_embeddings: cannot open %s — %s", h5_path, exc)
        return False


def _process_one(
    h5_path: str,
    encoder: "torch.nn.Module",
    output_dataset_name: str,
    device: str,
    force: bool,
) -> bool:
    """Process a single H5 file.

    Called only after ``_needs_processing`` returned True, so the file is
    guaranteed to need encoding.  Returns True on success, False on structural
    errors (missing datasets, IO failures, etc.).
    """
    # ------------------------------------------------------------------
    # Read tokenized inputs
    # ------------------------------------------------------------------
    try:
        with h5py.File(h5_path, "r") as f:
            if _TC_GROUP not in f:
                logger.warning(
                    "stage_embeddings: no '%s' group in %s — skipping",
                    _TC_GROUP,
                    h5_path,
                )
                return False

            grp = f[_TC_GROUP]

            if _DS_INPUT_IDS not in grp:
                logger.warning(
                    "stage_embeddings: '%s/%s' not found in %s — skipping",
                    _TC_GROUP,
                    _DS_INPUT_IDS,
                    h5_path,
                )
                return False

            input_ids = torch.tensor(grp[_DS_INPUT_IDS][:], dtype=torch.long)
            attention_mask = torch.tensor(grp[_DS_ATTN_MASK][:], dtype=torch.long)
            inverse_indices = torch.tensor(grp[_DS_INVERSE_IDX][:], dtype=torch.long)
    except Exception as exc:
        logger.warning(
            "stage_embeddings: error reading token data from %s — %s",
            h5_path,
            exc,
        )
        return False

    # ------------------------------------------------------------------
    # Encode unique patches
    # ------------------------------------------------------------------
    input_ids = input_ids.to(device)
    attention_mask = attention_mask.to(device)

    with torch.no_grad():
        with torch.autocast(device_type=device.split(":")[0], dtype=torch.bfloat16):
            unique_embeddings = encoder(input_ids, attention_mask)  # (P_unique, 384)

    # Move to CPU for numpy conversion
    unique_embeddings = unique_embeddings.float().cpu()

    # ------------------------------------------------------------------
    # Expand unique → per-patch embeddings via inverse indices
    # ------------------------------------------------------------------
    # inverse_indices[i] gives the index into unique_embeddings for patch i
    inverse_indices_cpu = inverse_indices.cpu()
    per_patch_emb = unique_embeddings[inverse_indices_cpu]  # (P, 384)

    per_patch_np = per_patch_emb.numpy().astype(np.float32)

    # ------------------------------------------------------------------
    # Write back to H5
    # ------------------------------------------------------------------
    try:
        write_embeddings_to_h5(
            h5_path=h5_path,
            dataset_name=output_dataset_name,
            embeddings=per_patch_np,
            force=force,
        )
    except Exception as exc:
        logger.error(
            "stage_embeddings: failed to write embeddings to %s — %s",
            h5_path,
            exc,
        )
        return False

    logger.info(
        "stage_embeddings: wrote text_conditioning/%s shape=%s to %s",
        output_dataset_name,
        per_patch_np.shape,
        h5_path,
    )
    return True
