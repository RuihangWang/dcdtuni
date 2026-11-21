import json
import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import h5py
import torch

logger = logging.getLogger(__name__)


_TOKENIZER_CACHE: Dict[Tuple[str, int], object] = {}


def _get_tokenizer(model_name: str):
    cache_key = (model_name, os.getpid())
    if cache_key in _TOKENIZER_CACHE:
        return _TOKENIZER_CACHE[cache_key]

    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=False)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    _TOKENIZER_CACHE[cache_key] = tokenizer
    return tokenizer


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    return text


def _normalize_ws(text: str) -> str:
    return " ".join(str(text).split())


def _split_top_level_statements(content: str) -> List[str]:
    content = _strip_comments(content)
    statements: List[str] = []
    buffer: List[str] = []
    round_depth = 0
    square_depth = 0
    curly_depth = 0

    for ch in content:
        if ch == "(":
            round_depth += 1
        elif ch == ")":
            round_depth = max(0, round_depth - 1)
        elif ch == "[":
            square_depth += 1
        elif ch == "]":
            square_depth = max(0, square_depth - 1)
        elif ch == "{":
            curly_depth += 1
        elif ch == "}":
            curly_depth = max(0, curly_depth - 1)

        if ch == ";" and round_depth == 0 and square_depth == 0 and curly_depth == 0:
            statement = _normalize_ws("".join(buffer))
            if statement:
                statements.append(statement)
            buffer = []
            continue

        buffer.append(ch)

    trailing = _normalize_ws("".join(buffer))
    if trailing:
        statements.append(trailing)
    return statements


def _statement_key_value(statement: str) -> Tuple[str, str]:
    statement = _normalize_ws(statement).rstrip(";")
    if not statement:
        return "", ""
    parts = statement.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _canonicalize_block_content(content: str) -> str:
    from TextCond.canonicalize import (
        strip_comments,
        strip_foamfile_header,
        strip_dimensions,
        expand_macros,
        abstract_crossrefs,
        normalize_numerics,
        normalize_whitespace,
        parse_statements,
        sort_statements,
        fill_bc_defaults,
        remove_redundant_fields,
    )

    content = strip_comments(content)
    content = strip_foamfile_header(content)
    content = strip_dimensions(content)
    content = expand_macros(content)
    content = abstract_crossrefs(content)
    content = normalize_numerics(content)
    content = normalize_whitespace(content)
    content = fill_bc_defaults(content)
    content = remove_redundant_fields(content)

    stmts = parse_statements(content)
    stmts = sort_statements(stmts)
    if not stmts:
        return "type unspecified;"
    return "\n".join(stmts)


def _canonicalize_free_text(text: str) -> str:
    from TextCond.global_canon import canonicalize_global_text

    return canonicalize_global_text(text)


def _normalize_patch_role(patch_name: str) -> str:
    from TextCond.classify import classify_patch_role

    return classify_patch_role(patch_name)


def _extract_patch_reference_edges(
    source_patch_name: str,
    statements: List[str],
    concrete_patch_names: Dict[str, int],
    edge_type_label: str,
) -> List[Tuple[int, int, str]]:
    source_idx = concrete_patch_names[source_patch_name]
    edges: List[Tuple[int, int, str]] = []
    for statement in statements:
        _, value = _statement_key_value(statement)
        if not value:
            continue
        normalized_value = value.strip().strip('"').rstrip(";")
        if normalized_value in concrete_patch_names:
            edges.append((source_idx, concrete_patch_names[normalized_value], edge_type_label))
    return edges


def _deduplicate_reference_edges(
    edges: List[Tuple[int, int, str]],
) -> Tuple[torch.Tensor, torch.Tensor, List[str]]:
    if not edges:
        return (
            torch.zeros((2, 0), dtype=torch.long),
            torch.zeros((0,), dtype=torch.long),
            [],
        )

    unique_edges = sorted(set(edges), key=lambda item: (item[0], item[1], item[2]))
    type_to_idx: Dict[str, int] = {}
    edge_index = torch.zeros((2, len(unique_edges)), dtype=torch.long)
    edge_type = torch.zeros((len(unique_edges),), dtype=torch.long)
    for edge_idx, (src, dst, edge_label) in enumerate(unique_edges):
        if edge_label not in type_to_idx:
            type_to_idx[edge_label] = len(type_to_idx)
        edge_index[0, edge_idx] = int(src)
        edge_index[1, edge_idx] = int(dst)
        edge_type[edge_idx] = int(type_to_idx[edge_label])

    type_vocab = [None] * len(type_to_idx)
    for label, idx in type_to_idx.items():
        type_vocab[idx] = label
    return edge_index, edge_type, type_vocab


def parse_poly_boundary_patch_names(boundary_path: str) -> List[str]:
    content = Path(boundary_path).read_text(errors="ignore")
    start = content.find("(")
    end = content.rfind(")")
    if start < 0 or end <= start:
        raise ValueError(f"Invalid polyMesh boundary file: {boundary_path}")

    body = content[start + 1:end]
    patch_names: List[str] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        token_start = i
        while i < n and not body[i].isspace() and body[i] != "{":
            i += 1
        patch_name = body[token_start:i].strip()
        if not patch_name:
            break

        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "{":
            break

        brace_depth = 0
        while i < n:
            if body[i] == "{":
                brace_depth += 1
            elif body[i] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    i += 1
                    break
            i += 1

        patch_names.append(patch_name)

    return patch_names


def parse_poly_boundary_patch_blocks(boundary_path: str) -> List[Tuple[str, str]]:
    content = Path(boundary_path).read_text(errors="ignore")
    start = content.find("(")
    end = content.rfind(")")
    if start < 0 or end <= start:
        raise ValueError(f"Invalid polyMesh boundary file: {boundary_path}")

    body = content[start + 1:end]
    blocks: List[Tuple[str, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        token_start = i
        while i < n and not body[i].isspace() and body[i] != "{":
            i += 1
        patch_name = body[token_start:i].strip()
        if not patch_name:
            break

        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "{":
            break

        brace_start = i
        brace_depth = 0
        while i < n:
            if body[i] == "{":
                brace_depth += 1
            elif body[i] == "}":
                brace_depth -= 1
                if brace_depth == 0:
                    content_start = brace_start + 1
                    content_end = i
                    blocks.append((patch_name, body[content_start:content_end].strip()))
                    i += 1
                    break
            i += 1

    return blocks


def _extract_boundary_field_content(field_text: str) -> str:
    start = field_text.find("boundaryField")
    if start < 0:
        raise ValueError("boundaryField block not found")
    brace_start = field_text.find("{", start)
    if brace_start < 0:
        raise ValueError("boundaryField opening brace not found")

    depth = 0
    end = -1
    for idx in range(brace_start, len(field_text)):
        ch = field_text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = idx
                break
    if end < 0:
        raise ValueError("boundaryField closing brace not found")

    return field_text[brace_start + 1:end]


def _parse_named_boundary_blocks(field_text: str) -> List[Tuple[str, str]]:
    body = _extract_boundary_field_content(field_text)
    blocks: List[Tuple[str, str]] = []
    i = 0
    n = len(body)
    while i < n:
        while i < n and body[i].isspace():
            i += 1
        if i >= n:
            break

        if body[i] == '"':
            i += 1
            token_start = i
            while i < n and body[i] != '"':
                i += 1
            name = body[token_start:i]
            i += 1
        else:
            token_start = i
            while i < n and not body[i].isspace() and body[i] != "{":
                i += 1
            name = body[token_start:i].strip()

        if not name:
            break

        while i < n and body[i].isspace():
            i += 1
        if i >= n or body[i] != "{":
            continue

        content_start = i + 1
        depth = 0
        while i < n:
            if body[i] == "{":
                depth += 1
            elif body[i] == "}":
                depth -= 1
                if depth == 0:
                    content = body[content_start:i].strip()
                    blocks.append((name, content))
                    i += 1
                    break
            i += 1

    return blocks


def _resolve_patch_block_content(
    patch_name: str,
    parsed_blocks: List[Tuple[str, str]],
) -> str:
    exact_map = {name: content for name, content in parsed_blocks if not _looks_like_regex(name)}
    if patch_name in exact_map:
        return exact_map[patch_name]

    for name, content in parsed_blocks:
        if _looks_like_regex(name):
            if re.fullmatch(name, patch_name):
                return content

    return "type unspecified;"


def _looks_like_regex(token: str) -> bool:
    return any(ch in token for ch in [".*", "^", "$", "[", "]", "(", ")", "+", "?"])


def _build_patch_texts(
    case_path: str,
    bc_fields: List[str],
    patch_name_mode: str = "normalized_role",
    canonicalize: bool = True,
    reference_graph: bool = True,
) -> Dict[str, object]:
    case_dir = Path(case_path)
    boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
    patch_blocks = parse_poly_boundary_patch_blocks(str(boundary_path))
    patch_order = [name for name, _ in patch_blocks]
    patch_name_to_index = {name: idx for idx, name in enumerate(patch_order)}

    parsed_field_blocks: Dict[str, List[Tuple[str, str]]] = {}
    for field_name in bc_fields:
        field_path = case_dir / "0" / field_name
        field_text = field_path.read_text(errors="ignore")
        parsed_field_blocks[field_name] = _parse_named_boundary_blocks(field_text)

    patch_texts: List[str] = []
    normalized_patch_roles: List[str] = []
    reference_edges: List[Tuple[int, int, str]] = []
    for patch_name, boundary_block in patch_blocks:
        role = _normalize_patch_role(patch_name)
        normalized_patch_roles.append(role)
        segments: List[str] = []
        if str(patch_name_mode) == "raw":
            segments.append(f"[patch_name]\n{patch_name}")
        elif str(patch_name_mode) == "normalized_role":
            segments.append(f"[patch_role]\n{role}")

        if reference_graph:
            reference_edges.extend(
                _extract_patch_reference_edges(
                    source_patch_name=patch_name,
                    statements=_split_top_level_statements(boundary_block),
                    concrete_patch_names=patch_name_to_index,
                    edge_type_label="boundary.neighbourPatch",
                )
            )
        for field_name in bc_fields:
            block_content = _resolve_patch_block_content(
                patch_name,
                parsed_field_blocks[field_name],
            )
            canonical_content = (
                _canonicalize_block_content(block_content)
                if canonicalize
                else _canonicalize_free_text(block_content)
            )
            segments.append(f"[{field_name}]")
            segments.append(canonical_content)
            if reference_graph:
                reference_edges.extend(
                    _extract_patch_reference_edges(
                        source_patch_name=patch_name,
                        statements=_split_top_level_statements(block_content),
                        concrete_patch_names=patch_name_to_index,
                        edge_type_label=f"field.{field_name}",
                    )
                )
        patch_texts.append("\n".join(segments))

    patch_ref_edge_index, patch_ref_edge_type, patch_ref_type_vocab = _deduplicate_reference_edges(
        reference_edges
    )

    return {
        "patch_order": patch_order,
        "raw_patch_names": list(patch_order),
        "normalized_patch_roles": normalized_patch_roles,
        "patch_texts": patch_texts,
        "patch_name_to_patch_index": patch_name_to_index,
        "patch_ref_edge_index": patch_ref_edge_index,
        "patch_ref_edge_type": patch_ref_edge_type,
        "patch_ref_type_vocab": patch_ref_type_vocab,
    }


def _build_global_text(case_path: str, global_files: List[str]) -> str:
    case_dir = Path(case_path)
    chunks: List[str] = []
    for relative_path in global_files:
        file_path = case_dir / relative_path
        content = file_path.read_text(errors="ignore")
        chunks.append(f"[{relative_path}]\n{_canonicalize_free_text(content)}")
    return "\n\n".join(chunks)


def _tokenize_texts(
    tokenizer,
    texts: List[str],
    max_length: int,
) -> Tuple[torch.Tensor, torch.Tensor]:
    effective_max_length = int(max_length)
    tokenizer_limit = getattr(tokenizer, "model_max_length", None)
    if isinstance(tokenizer_limit, int) and 0 < tokenizer_limit < 100000:
        effective_max_length = min(effective_max_length, int(tokenizer_limit))
    encoded = tokenizer(
        texts,
        padding="max_length",
        truncation=True,
        max_length=effective_max_length,
        return_tensors="pt",
    )
    return encoded["input_ids"].to(torch.long), encoded["attention_mask"].to(torch.long)


def _load_patch_spatial_geometry(h5_path: str) -> Dict[str, Dict[str, List[float]]]:
    """Load per-patch mean position (mean-centered) and mean unit normal from H5.

    Position is mean-centered (subtract global centroid of all cpd_cells) to
    remove arbitrary origin placement while preserving absolute domain scale.
    Unit normals are L2-normalized means of per-face normals.

    Returns dict: patch_name -> {"mean_pos": [x,y,z], "mean_unv": [x,y,z]}
    """
    result: Dict[str, Dict[str, List[float]]] = {}
    with h5py.File(h5_path, "r") as f:
        if "cpd|centroid" not in f:
            return result

        cpd_centroid = torch.from_numpy(f["cpd|centroid"][()]).to(torch.float32)
        num_cpd_cell = cpd_centroid.shape[0]

        # Compute per-cpd-cell unit normals (mirrors HomoGraph_dataset.py:731-752)
        cells_face_unv = torch.from_numpy(f["cells_face_normal"][()]).to(torch.float32)
        cell_face = torch.from_numpy(f["cells_face"][()]).long()
        face_type = torch.from_numpy(f["face|face_type"][()]).long().squeeze()
        cell_type = torch.from_numpy(f["cpd|cell_type"][()]).long().squeeze()
        num_face = face_type.shape[0]

        unv_face = torch.zeros(num_face, 3, dtype=torch.float32)
        unv_face[cell_face] = cells_face_unv

        mask_boundary_cell = cell_type != 0  # FaceType.NORMAL = 0
        mask_boundary_face = face_type != 0

        unv_cpd_cell = torch.zeros(num_cpd_cell, 3, dtype=torch.float32)
        unv_cpd_cell[mask_boundary_cell] = unv_face[mask_boundary_face]

        # Mean-centering: subtract global centroid to remove arbitrary origin
        global_centroid = cpd_centroid.mean(dim=0)

        # Load patch list
        raw = f.attrs.get("patch_list", "{}")
        patch_list = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode())

        for patch_name, meta in patch_list.items():
            start = int(meta.get("start_idx_cpd_cell", 0))
            end = int(meta.get("end_idx_cpd_cell", 0))
            if end <= start:
                continue

            mean_pos = cpd_centroid[start:end].mean(dim=0)
            mean_pos_centered = (mean_pos - global_centroid).tolist()

            mean_unv = unv_cpd_cell[start:end].mean(dim=0)
            unv_norm = mean_unv.norm().item()
            if unv_norm > 1e-6:
                mean_unv = (mean_unv / unv_norm).tolist()
            else:
                mean_unv = mean_unv.tolist()

            result[patch_name] = {
                "mean_pos": [round(v, 6) for v in mean_pos_centered],
                "mean_unv": [round(v, 6) for v in mean_unv],
            }

    return result


def _build_sidecar_from_toml(
    case_path: str,
    model_name: str,
    bc_fields: List[str],
    global_files: List[str],
    patch_max_len: int,
    global_max_len: int,
    reference_graph: bool = True,
    h5_path: Optional[str] = None,
) -> Dict[str, object]:
    """Build text conditioning sidecar using TOML codebook serialization."""
    from TextCond.toml_codebook import of_case_to_toml, serialize_patch_toml, serialize_case_toml

    toml_data = of_case_to_toml(case_path, bc_fields=bc_fields)

    # Inject spatial geometry if H5 available
    if h5_path and os.path.isfile(h5_path):
        patch_geometry = _load_patch_spatial_geometry(h5_path)
        for patch_name in toml_data:
            if patch_name != "global_properties" and patch_name in patch_geometry:
                toml_data[patch_name]["geometry"] = patch_geometry[patch_name]

    # Build patch texts and ordering
    patch_order = []
    patch_texts = []
    for name, pdict in toml_data.items():
        if name == "global_properties":
            continue
        patch_order.append(name)
        patch_texts.append(serialize_patch_toml(pdict))

    # Build global text (whole-case serialization)
    global_text = serialize_case_toml(toml_data)

    # Deduplicate patch texts
    unique_texts: List[str] = []
    unique_indices: Dict[str, int] = {}
    inverse_indices: List[int] = []
    for text in patch_texts:
        idx = unique_indices.get(text)
        if idx is None:
            idx = len(unique_texts)
            unique_indices[text] = idx
            unique_texts.append(text)
        inverse_indices.append(idx)

    # Tokenize
    tokenizer = _get_tokenizer(model_name)
    patch_input_ids, patch_attention_mask = _tokenize_texts(
        tokenizer, patch_texts, patch_max_len
    )
    patch_unique_input_ids, patch_unique_attention_mask = _tokenize_texts(
        tokenizer, unique_texts, patch_max_len
    )
    global_input_ids, global_attention_mask = _tokenize_texts(
        tokenizer, [global_text], global_max_len
    )

    # Build reference graph (patch coupling edges from TOML meta.coupled_patch)
    patch_ref_edge_index = torch.zeros(2, 0, dtype=torch.long)
    patch_ref_edge_type = torch.zeros(0, dtype=torch.long)
    patch_ref_type_vocab: List[str] = []
    if reference_graph:
        name_to_idx = {n: i for i, n in enumerate(patch_order)}
        src_list, dst_list, type_list = [], [], []
        type_vocab = {"thermal_coupling": 0}
        for i, name in enumerate(patch_order):
            coupled = toml_data[name].get("meta", {}).get("coupled_patch")
            if coupled and coupled in name_to_idx:
                src_list.append(i)
                dst_list.append(name_to_idx[coupled])
                type_list.append(0)
                # Bidirectional
                src_list.append(name_to_idx[coupled])
                dst_list.append(i)
                type_list.append(0)
        if src_list:
            patch_ref_edge_index = torch.tensor([src_list, dst_list], dtype=torch.long)
            patch_ref_edge_type = torch.tensor(type_list, dtype=torch.long)
        patch_ref_type_vocab = list(type_vocab.keys())

    return {
        "patch_order": patch_order,
        "raw_patch_names": patch_order,
        "normalized_patch_roles": [
            toml_data[n].get("meta", {}).get("role", "unknown") for n in patch_order
        ],
        "patch_name_to_patch_index": {n: i for i, n in enumerate(patch_order)},
        "patch_texts": patch_texts,
        "canonical_patch_texts": patch_texts,
        "patch_input_ids": patch_input_ids,
        "patch_attention_mask": patch_attention_mask,
        "patch_unique_texts": unique_texts,
        "canonical_patch_unique_texts": unique_texts,
        "patch_unique_input_ids": patch_unique_input_ids,
        "patch_unique_attention_mask": patch_unique_attention_mask,
        "patch_text_inverse_indices": torch.tensor(inverse_indices, dtype=torch.long),
        "patch_ref_edge_index": patch_ref_edge_index,
        "patch_ref_edge_type": patch_ref_edge_type,
        "patch_ref_type_vocab": patch_ref_type_vocab,
        "global_text": global_text,
        "global_input_ids": global_input_ids,
        "global_attention_mask": global_attention_mask,
        "text_encoder_name": model_name,
        "tokenizer_name": model_name,
        "encoded_fields": list(bc_fields),
        "global_files": list(global_files),
        "patch_name_mode": "toml_codebook",
        "canonicalize": True,
        "reference_graph": reference_graph,
        "text_source": "toml_codebook",
    }


def build_text_conditioning_sidecar(
    case_path: str,
    model_name: str,
    bc_fields: List[str],
    global_files: List[str],
    patch_max_len: int,
    global_max_len: int,
    patch_name_mode: str = "normalized_role",
    canonicalize: bool = True,
    reference_graph: bool = True,
    text_source: str = "openfoam_raw",
    h5_path: Optional[str] = None,
) -> Dict[str, object]:
    if text_source == "toml_codebook":
        return _build_sidecar_from_toml(
            case_path=case_path,
            model_name=model_name,
            bc_fields=bc_fields,
            global_files=global_files,
            patch_max_len=patch_max_len,
            global_max_len=global_max_len,
            reference_graph=reference_graph,
            h5_path=h5_path,
        )
    # else: fall through to existing openfoam_raw logic
    patch_payload = _build_patch_texts(
        case_path,
        bc_fields,
        patch_name_mode=patch_name_mode,
        canonicalize=canonicalize,
        reference_graph=reference_graph,
    )
    patch_order = patch_payload["patch_order"]
    patch_texts = patch_payload["patch_texts"]
    global_text = _build_global_text(case_path, global_files)

    tokenizer = _get_tokenizer(model_name)
    patch_input_ids, patch_attention_mask = _tokenize_texts(tokenizer, patch_texts, patch_max_len)
    global_input_ids, global_attention_mask = _tokenize_texts(tokenizer, [global_text], global_max_len)

    unique_texts: List[str] = []
    unique_indices: Dict[str, int] = {}
    inverse_indices: List[int] = []
    for text in patch_texts:
        idx = unique_indices.get(text)
        if idx is None:
            idx = len(unique_texts)
            unique_indices[text] = idx
            unique_texts.append(text)
        inverse_indices.append(idx)

    patch_unique_input_ids, patch_unique_attention_mask = _tokenize_texts(
        tokenizer, unique_texts, patch_max_len
    )

    return {
        "patch_order": patch_order,
        "raw_patch_names": patch_payload["raw_patch_names"],
        "normalized_patch_roles": patch_payload["normalized_patch_roles"],
        "patch_name_to_patch_index": patch_payload["patch_name_to_patch_index"],
        "patch_texts": patch_texts,
        "canonical_patch_texts": patch_texts,
        "patch_input_ids": patch_input_ids,
        "patch_attention_mask": patch_attention_mask,
        "patch_unique_texts": unique_texts,
        "canonical_patch_unique_texts": unique_texts,
        "patch_unique_input_ids": patch_unique_input_ids,
        "patch_unique_attention_mask": patch_unique_attention_mask,
        "patch_text_inverse_indices": torch.tensor(inverse_indices, dtype=torch.long),
        "patch_ref_edge_index": patch_payload["patch_ref_edge_index"],
        "patch_ref_edge_type": patch_payload["patch_ref_edge_type"],
        "patch_ref_type_vocab": patch_payload["patch_ref_type_vocab"],
        "global_text": global_text,
        "global_input_ids": global_input_ids,
        "global_attention_mask": global_attention_mask,
        "text_encoder_name": model_name,
        "tokenizer_name": model_name,
        "encoded_fields": list(bc_fields),
        "global_files": list(global_files),
        "patch_name_mode": str(patch_name_mode),
        "canonicalize": bool(canonicalize),
        "reference_graph": bool(reference_graph),
    }


def _string_array(values: List[str]):
    return values


def save_text_conditioning_to_h5(
    h5_path: str,
    text_data: Dict[str, object],
    group_name: str = "text_conditioning",
) -> str:
    string_dtype = h5py.string_dtype(encoding="utf-8")
    new_source = str(text_data.get("text_source", "openfoam_raw"))
    with h5py.File(h5_path, "a") as f:
        if group_name in f:
            existing_source = f[group_name].attrs.get("text_source", "openfoam_raw")
            if isinstance(existing_source, bytes):
                existing_source = existing_source.decode("utf-8")
            if str(existing_source) != new_source:
                logger.warning(
                    "Overwriting existing %s group in %s (text_source: %s -> %s). "
                    "Any checkpoints trained on the old sidecar may produce wrong results.",
                    group_name, h5_path, existing_source, new_source,
                )
            del f[group_name]
        group = f.create_group(group_name)

        group.attrs["text_encoder_name"] = str(text_data["text_encoder_name"])
        group.attrs["tokenizer_name"] = str(text_data["tokenizer_name"])
        group.attrs["encoded_fields"] = json.dumps(list(text_data["encoded_fields"]))
        group.attrs["global_files"] = json.dumps(list(text_data["global_files"]))
        group.attrs["patch_name_mode"] = str(text_data.get("patch_name_mode", "normalized_role"))
        group.attrs["canonicalize"] = bool(text_data.get("canonicalize", True))
        group.attrs["reference_graph"] = bool(text_data.get("reference_graph", True))
        if "text_source" in text_data:
            group.attrs["text_source"] = str(text_data["text_source"])

        group.create_dataset(
            "patch_order",
            data=_string_array(list(text_data["patch_order"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "raw_patch_names",
            data=_string_array(list(text_data["raw_patch_names"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "normalized_patch_roles",
            data=_string_array(list(text_data["normalized_patch_roles"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "patch_texts",
            data=_string_array(list(text_data["patch_texts"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "canonical_patch_texts",
            data=_string_array(list(text_data["canonical_patch_texts"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "patch_unique_texts",
            data=_string_array(list(text_data["patch_unique_texts"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "canonical_patch_unique_texts",
            data=_string_array(list(text_data["canonical_patch_unique_texts"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "patch_name_to_patch_index_json",
            data=json.dumps(text_data["patch_name_to_patch_index"]),
            dtype=string_dtype,
        )
        group.create_dataset(
            "patch_ref_type_vocab_json",
            data=json.dumps(list(text_data["patch_ref_type_vocab"])),
            dtype=string_dtype,
        )
        group.create_dataset(
            "global_text",
            data=str(text_data["global_text"]),
            dtype=string_dtype,
        )

        tensor_keys = [
            "patch_input_ids",
            "patch_attention_mask",
            "patch_unique_input_ids",
            "patch_unique_attention_mask",
            "patch_text_inverse_indices",
            "patch_ref_edge_index",
            "patch_ref_edge_type",
            "global_input_ids",
            "global_attention_mask",
        ]
        for key in tensor_keys:
            value = text_data[key]
            if isinstance(value, torch.Tensor):
                value = value.detach().cpu().numpy()
            group.create_dataset(key, data=value)

    return h5_path


def load_text_conditioning_from_h5(
    h5_path: str,
    group_name: str = "text_conditioning",
) -> Dict[str, object]:
    with h5py.File(h5_path, "r") as f:
        if group_name not in f:
            raise KeyError(f"Missing H5 group '{group_name}' in {h5_path}")
        group = f[group_name]

        def _read_string_array(name: str) -> List[str]:
            raw = group[name][()]
            if isinstance(raw, bytes):
                return [raw.decode("utf-8")]
            values = raw.tolist() if hasattr(raw, "tolist") else list(raw)
            result = []
            for item in values:
                if isinstance(item, bytes):
                    result.append(item.decode("utf-8"))
                else:
                    result.append(str(item))
            return result

        def _read_string_scalar(name: str) -> str:
            raw = group[name][()]
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

        return {
            "patch_order": _read_string_array("patch_order"),
            "raw_patch_names": _read_string_array("raw_patch_names") if "raw_patch_names" in group else _read_string_array("patch_order"),
            "normalized_patch_roles": _read_string_array("normalized_patch_roles") if "normalized_patch_roles" in group else [_normalize_patch_role(name) for name in _read_string_array("patch_order")],
            "patch_texts": _read_string_array("patch_texts"),
            "canonical_patch_texts": _read_string_array("canonical_patch_texts") if "canonical_patch_texts" in group else _read_string_array("patch_texts"),
            "patch_unique_texts": _read_string_array("patch_unique_texts"),
            "canonical_patch_unique_texts": _read_string_array("canonical_patch_unique_texts") if "canonical_patch_unique_texts" in group else _read_string_array("patch_unique_texts"),
            "patch_name_to_patch_index": json.loads(_read_string_scalar("patch_name_to_patch_index_json")),
            "global_text": _read_string_scalar("global_text"),
            "patch_input_ids": torch.from_numpy(group["patch_input_ids"][()]).to(torch.long),
            "patch_attention_mask": torch.from_numpy(group["patch_attention_mask"][()]).to(torch.long),
            "patch_unique_input_ids": torch.from_numpy(group["patch_unique_input_ids"][()]).to(torch.long),
            "patch_unique_attention_mask": torch.from_numpy(group["patch_unique_attention_mask"][()]).to(torch.long),
            "patch_text_inverse_indices": torch.from_numpy(group["patch_text_inverse_indices"][()]).to(torch.long),
            "patch_ref_edge_index": torch.from_numpy(group["patch_ref_edge_index"][()]).to(torch.long) if "patch_ref_edge_index" in group else torch.zeros((2, 0), dtype=torch.long),
            "patch_ref_edge_type": torch.from_numpy(group["patch_ref_edge_type"][()]).to(torch.long) if "patch_ref_edge_type" in group else torch.zeros((0,), dtype=torch.long),
            "patch_ref_type_vocab": json.loads(_read_string_scalar("patch_ref_type_vocab_json")) if "patch_ref_type_vocab_json" in group else [],
            "global_input_ids": torch.from_numpy(group["global_input_ids"][()]).to(torch.long),
            "global_attention_mask": torch.from_numpy(group["global_attention_mask"][()]).to(torch.long),
            "text_encoder_name": str(group.attrs["text_encoder_name"]),
            "tokenizer_name": str(group.attrs["tokenizer_name"]),
            "encoded_fields": json.loads(str(group.attrs["encoded_fields"])),
            "global_files": json.loads(str(group.attrs["global_files"])),
            "patch_name_mode": str(group.attrs["patch_name_mode"]) if "patch_name_mode" in group.attrs else "normalized_role",
            "canonicalize": bool(group.attrs["canonicalize"]) if "canonicalize" in group.attrs else True,
            "reference_graph": bool(group.attrs["reference_graph"]) if "reference_graph" in group.attrs else bool("patch_ref_edge_index" in group),
            "text_source": str(group.attrs["text_source"]) if "text_source" in group.attrs else "openfoam_raw",
        }


def build_and_save_text_conditioning_sidecar(
    case_path: str,
    h5_path: str,
    model_name: str,
    bc_fields: List[str],
    global_files: List[str],
    patch_max_len: int,
    global_max_len: int,
    patch_name_mode: str = "normalized_role",
    canonicalize: bool = True,
    reference_graph: bool = True,
    output_name: str = "text_inputs.pt",
    text_source: str = "openfoam_raw",
) -> str:
    sidecar_data = build_text_conditioning_sidecar(
        case_path=case_path,
        model_name=model_name,
        bc_fields=bc_fields,
        global_files=global_files,
        patch_max_len=patch_max_len,
        global_max_len=global_max_len,
        patch_name_mode=patch_name_mode,
        canonicalize=canonicalize,
        reference_graph=reference_graph,
        text_source=text_source,
        h5_path=h5_path,
    )
    legacy_output_path = Path(case_path) / "h5" / output_name
    if legacy_output_path.exists():
        legacy_output_path.unlink()
    return save_text_conditioning_to_h5(h5_path, sidecar_data)


def load_patch_list_from_h5(h5_path: str) -> Dict[str, Dict]:
    with h5py.File(h5_path, "r") as f:
        raw = f.attrs["patch_list"]
    return json.loads(raw)
