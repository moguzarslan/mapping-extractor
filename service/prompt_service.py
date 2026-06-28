
import json
import re

from infra.document_service import read_document
from utils.image_transformer import ImageTransformer
from utils.json_service import extract_json_from_response, save_json, extract_json_from_file
from resource.prompts.prompts import Prompts
from infra.gemini_client import ask_gemini


from typing import Union, List
from google.genai import types


def build_document_prompt(
        file: str,
        prompt: str,
        image_folder: str,
) -> Union[str, list]:
    """
    Builds:
    - text-only prompt
    - or multimodal prompt with text + images
    """
    document_text = read_document(file)

    full_text = f"""
{prompt}

Document:
\"\"\"
{document_text}
\"\"\"
""".strip()

    content = [
        types.Part.from_text(text=full_text)
    ]

    if image_folder:
        content.extend(ImageTransformer.from_folder(image_folder))

    return content

def build_chained_json_prompt(
        prompt: str,
        requirements_json: str,
        architecture_json: str,
        image_folder: str = None,
) -> Union[str, list]:
    """
    Builds:
    - multimodal prompt (text + images if provided)
    """

    full_text = f"""
{prompt}

INPUT DATA:
{{
   {requirements_json},
   {architecture_json}
}}
""".strip()

    content = [{"type": "text", "text": full_text}]
    return content

def build_document_json_prompt(
        file: str,
        prompt: str,
        json_payload: str,
) -> Union[str, list]:
    """
    Builds a text prompt combining the document text and an injected JSON payload
    (the already-extracted Architectural Units). Used by the connector pass, which
    needs the unit ids to link each connector's two endpoints.
    """
    document_text = read_document(file)

    full_text = f"""
{prompt}

ALREADY-EXTRACTED ARCHITECTURAL UNITS (JSON):
{json_payload}

Document:
\"\"\"
{document_text}
\"\"\"
""".strip()

    content = [types.Part.from_text(text=full_text)]

    return content

def build_requirements_json_prompt(
        prompt: str,
        requirements_json: str,
) -> Union[str, list]:
    """
    Builds a JSON-only prompt (no document, no images) that appends an
    extracted-requirements JSON for post-processing steps such as splitting.
    `requirements_json` must already be a valid JSON string.
    """

    full_text = f"""
{prompt}

INPUT JSON:
{requirements_json}
""".strip()

    content = [types.Part.from_text(text=full_text)]
    return content

def build_validation_prompt(
        file: str,
        prompt: str,
        json: str,
) -> Union[str, list]:

    document_text = read_document(file)

    full_text = f"""
{prompt}

INPUT JSON:
{{
   {json}
}}

DOCUMENT:
{{
   {document_text}
}}
""".strip()

    content = [{"type": "text", "text": full_text}]
    return content
def process_single_prompt(file: str, folder: str, prompt: str, output_dir: str = "outputs", output_file_name: str = None) -> str:
    print(f"Processing: {folder}")
    prompt = build_document_prompt(file, prompt, image_folder=folder)
    response = ask_gemini(
        user_prompt=prompt,
    )
    output_path = save_result(
        file=output_file_name if output_file_name else file,
        output_dir=output_dir,
        response=response
    )
    print("Single prompt completed, results are saved successfully")
    return str(output_path)


def _architecture_group(data, key: str) -> list:
    """Pull a named group list (architectural_units / patterns) from a loaded JSON.

    Tolerates the model returning the list directly, the expected dict shape, or
    a dict whose only list value is the group.
    """
    if isinstance(data, dict):
        if isinstance(data.get(key), list):
            return data[key]
        lists = [v for v in data.values() if isinstance(v, list)]
        return lists[0] if len(lists) == 1 else []
    return data or []


_AU_ID = re.compile(r"AU[_-]?(\d+)", re.IGNORECASE)


def merge_connectors(units: list, connectors: list) -> list:
    """Append the connector records to the unit list.

    The connector pass already numbers connectors by continuing the units' AU
    sequence; here each is re-numbered defensively to the next free AU id so the
    final file is guaranteed collision-free even if the model miscounts or repeats
    an id. A connector's isPartOf references existing unit ids (the two units it
    links), which are left untouched.
    """
    units = list(units)
    max_idx = 0
    for u in units:
        m = _AU_ID.search(str(u.get("id") or ""))
        if m:
            max_idx = max(max_idx, int(m.group(1)))

    appended = []
    for c in connectors:
        if not isinstance(c, dict):
            continue
        max_idx += 1
        c = dict(c)
        c["id"] = f"AU_{max_idx:02d}"
        c["type"] = c.get("type") or "Connector"
        parents = c.get("isPartOf") or []
        if isinstance(parents, str):
            parents = [parents]
        c["isPartOf"] = [str(p).strip() for p in parents]
        appended.append(c)
    return units + appended


def extract_architecture_group(file: str, prompt: str, group_key: str) -> list:
    """Run a document-only architecture prompt and return its group list
    (architectural_units or patterns). Nothing is written to disk."""
    print(f"Extracting {group_key} from: {file}")
    built = build_document_prompt(file, prompt, image_folder=None)
    response = ask_gemini(user_prompt=built)
    return _architecture_group(extract_json_from_response(response), group_key)


def extract_connectors(file: str, units: list) -> list:
    """Run the connector prompt with the document and the already-extracted units;
    return the connector records. Nothing is written to disk.

    The units (id/type/name/description) are sent so the model can link each
    connector's two endpoints by id.
    """
    print(f"Extracting connectors from: {file}")
    units_payload = json.dumps(
        {"architectural_units": [
            {"id": u.get("id"), "type": u.get("type"),
             "name": u.get("name"), "description": u.get("description")}
            for u in units]},
        ensure_ascii=False, indent=2,
    )
    built = build_document_json_prompt(
        file=file,
        prompt=Prompts.CONNECTOR_EXTRACTION_PROMPT,
        json_payload=units_payload,
    )
    response = ask_gemini(user_prompt=built)
    return _architecture_group(extract_json_from_response(response), "connectors")


def _reduce_for_linking(elements: list) -> list:
    """Reduce units/patterns to the fields the linking step needs to reason about
    containment: id (to reference), type, name and description (to decide it)."""
    return [
        {"id": e.get("id"), "type": e.get("type"),
         "name": e.get("name"), "description": e.get("description")}
        for e in elements
    ]


def _links_to_map(data) -> dict:
    """Normalise the linking response into {element id: [isPartOf ids]}.

    Tolerates the model returning {"isPartOf": [...]}, the list directly, or a dict
    whose only list value is the entries.
    """
    if isinstance(data, dict):
        items = data.get("isPartOf")
        if not isinstance(items, list):
            lists = [v for v in data.values() if isinstance(v, list)]
            items = lists[0] if len(lists) == 1 else []
    else:
        items = data or []

    out: dict = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        eid = it.get("id")
        if eid is None:
            continue
        parents = it.get("isPartOf") or []
        if isinstance(parents, str):
            parents = [parents]
        out[str(eid).strip()] = [str(p).strip() for p in parents if str(p).strip()]
    return out


def apply_ispartof(elements: list, links: dict) -> list:
    """Overwrite each element's isPartOf with the linking result, matched by id.
    Elements the linking step did not return keep their existing isPartOf."""
    out = []
    for e in elements:
        e = dict(e)
        eid = str(e.get("id")).strip() if e.get("id") is not None else None
        if eid is not None and eid in links:
            e["isPartOf"] = links[eid]
        out.append(e)
    return out


def extract_ispartof_links(file: str, units: list, patterns: list) -> dict:
    """Run the dedicated containment pass over the already-extracted units and
    patterns and return {id: [isPartOf ids]}.

    Only the reduced fields (id/type/name/description) of both groups are sent, with
    the document, so the model can build cross-namespace links (unit -> unit,
    unit -> pattern, pattern -> pattern) referring to elements by their AU_xx / P_xx
    ids. Connectors are not included — they keep their own endpoint isPartOf.
    """
    print(f"Extracting isPartOf links from: {file}")
    payload = json.dumps(
        {"architectural_units": _reduce_for_linking(units),
         "patterns": _reduce_for_linking(patterns)},
        ensure_ascii=False, indent=2,
    )
    built = build_document_json_prompt(
        file=file,
        prompt=Prompts.ISPARTOF_LINKING_PROMPT,
        json_payload=payload,
    )
    response = ask_gemini(user_prompt=built)
    return _links_to_map(extract_json_from_response(response))


def save_architecture(units: list, connectors: list, patterns: list,
                      output_file_name: str = "architecture",
                      output_dir: str = "outputs") -> str:
    """Merge the connectors into the units, combine with the patterns, and write the
    single canonical architecture file — the ONLY file the architecture pass saves.

    Units use the AU_xx id namespace (connectors continue it) and patterns use P_xx.
    isPartOf may reference either namespace (the linking pass builds unit->pattern
    links), so this is a straight concatenation into the {architectural_units,
    patterns} shape the evaluator expects.
    """
    architecture = {
        "architectural_units": merge_connectors(units, connectors),
        "patterns": patterns,
    }
    output_path = save_json(architecture, output_file_name, output_dir)
    print(f"Architecture saved: {output_path}")
    return str(output_path)


def process_chained_prompt(file: str, folder: str, final_prompt: str, output_dir: str = "outputs") -> None:

    print(f"Processing: {folder}")
    requirements_prompt = build_document_prompt(file, Prompts.REQUIREMENT_EXTRACTION_PROMPT, None)
    requirements_response = ask_gemini(
        user_prompt=requirements_prompt
    )
    save_result('requirement', output_dir, requirements_response)
    print("Requirements saved successfully")

    architecture_prompt = build_document_prompt(file, Prompts.ARCHITECTURE_EXTRACTION_PROMPT, image_folder=folder)
    architecture_response = ask_gemini(
        user_prompt=architecture_prompt
    )
    save_result('architecture', output_dir, architecture_response)
    print("Architectural items saved successfully")

    mapping_prompt = build_chained_json_prompt(
        prompt=final_prompt,
        architecture_json=architecture_response,
        requirements_json=requirements_response, )

    response = ask_gemini(
        user_prompt=mapping_prompt,
    )
    save_result('mapping', output_dir, response)
    print("Chained prompt completed, results are saved successfully")

def _as_requirements_list(data) -> list:
    """Normalise a loaded requirements JSON (list or {"requirements": [...]}) to a list."""
    if isinstance(data, dict):
        return data.get("requirements", [])
    return data or []


def _reduce_for_splitting(requirements: list) -> list:
    """Build the splitting input: id + description, plus relatedTo when present.

    relatedTo is sent so the model can re-point any link whose target id changes
    when that target is split (e.g. R_17 -> R_17a / R_17b); without it the model
    cannot see — let alone fix — references that the split would leave dangling.
    """
    reduced = []
    for r in requirements:
        item = {"id": r.get("id"), "description": r.get("description")}
        related = r.get("relatedTo")
        if related:
            item["relatedTo"] = related
        reduced.append(item)
    return reduced


def _parent_requirement_id(split_id: str, original_by_id: dict) -> Union[str, None]:
    """Resolve a (possibly suffixed) split id back to its original requirement id.

    R_01a / R_01b -> R_01 ; an unsplit R_02 -> R_02. Resolution prefers an exact
    match, then the longest original id that is a prefix followed only by the
    appended lowercase suffix, and finally a plain trailing-suffix strip.
    """
    if not split_id:
        return None
    if split_id in original_by_id:
        return split_id
    best = None
    for oid in original_by_id:
        suffix = split_id[len(oid):]
        if split_id.startswith(oid) and suffix.isalpha() and suffix.islower():
            if best is None or len(oid) > len(best):
                best = oid
    if best is not None:
        return best
    stripped = re.sub(r"[a-z]+$", "", split_id)
    return stripped if stripped in original_by_id else None


def _append_fix(fixes, label: str) -> list:
    """Return `fixes` as a list with `label` appended (no duplicates)."""
    if not fixes:
        normalized = []
    elif isinstance(fixes, list):
        normalized = list(fixes)
    else:
        normalized = [fixes]
    if label not in normalized:
        normalized.append(label)
    return normalized


def merge_split_requirements(original: list, split: list) -> list:
    """Re-attach the non-text fields onto the split requirements.

    The splitting step returns id + description (+ a possibly re-pointed
    relatedTo). For each returned element we copy every other field (type,
    pageNumber, concept, categorization, fixes, ...) from its parent requirement,
    then overwrite id and description with the split values. relatedTo is taken
    from the split output when the model provided it (it re-points links whose
    target id changed during splitting) and otherwise inherited from the parent.
    Parts that actually resulted from a split (their id differs from the parent
    id, e.g. R_01a from R_01) get "Split" appended to their "fixes" field here in
    code, not via the prompt.
    """
    original_by_id = {r["id"]: r for r in original if r.get("id")}
    merged = []
    for s in split:
        sid = s.get("id")
        sdesc = s.get("description", s.get("requirement"))
        parent_id = _parent_requirement_id(sid, original_by_id)
        record = dict(original_by_id.get(parent_id, {})) if parent_id else {}
        if not record:
            print(f"Warning: no parent requirement found for split id '{sid}'.")
        if sid is not None:
            record["id"] = sid
        if sdesc is not None:
            record["description"] = sdesc
        if "relatedTo" in s:
            # The model re-points relatedTo when a referenced requirement is
            # split, so honour its value rather than the parent's stale one.
            record["relatedTo"] = s.get("relatedTo")
        if parent_id is not None and sid is not None and sid != parent_id:
            record["fixes"] = _append_fix(record.get("fixes"), "Split")
        merged.append(record)
    return merged


def process_requirement_splitting(input_json_dir: str, output_file_name: str = "requirement_split", output_dir: str = "outputs") -> str:

    print(f"Processing requirement splitting: {input_json_dir}")
    original = _as_requirements_list(extract_json_from_file(input_json_dir))

    # Send id + description (+ relatedTo) to the model so it can realign links.
    reduced = _reduce_for_splitting(original)
    reduced_json = json.dumps({"requirements": reduced}, ensure_ascii=False, indent=2)
    split_prompt = build_requirements_json_prompt(Prompts.REQUIREMENT_SPLITTING_PROMPT, reduced_json)

    split_response = ask_gemini(
        user_prompt=split_prompt
    )

    # Parse the model output and copy the remaining fields back onto split ids.
    split_items = _as_requirements_list(extract_json_from_response(split_response))
    merged = merge_split_requirements(original, split_items)

    output_path = save_json(merged, output_file_name, output_dir)
    print(f"Requirement splitting saved successfully for: {input_json_dir}")
    return str(output_path)


def _is_criterion(record: dict) -> bool:
    return str(record.get("type", "")).strip().lower() == "criterion"


def _reduce_criteria_with_related(requirements: list) -> list:
    """Build the cleanup input: each criterion as id + description, plus its
    related requirement's description as read-only context for comparison."""
    by_id = {r.get("id"): r for r in requirements if r.get("id")}
    reduced = []
    for r in requirements:
        if not _is_criterion(r):
            continue
        item = {"id": r.get("id"), "description": r.get("description")}
        related = by_id.get(r.get("relatedTo"))
        if related is not None:
            item["relatedRequirement"] = related.get("description")
        reduced.append(item)
    return reduced


def filter_cleaned_criteria(original: list, cleaned: list) -> list:
    """Drop the criteria the cleanup step removed, keeping everything else intact.

    Only entries whose type is "criterion" may be removed: a criterion survives
    only if its id is still present in the model's cleaned output. Every other
    requirement (FR, QR, constraint) is always kept, with its original field
    values and order preserved — the model's keep/remove decision is the only
    thing trusted here, so nothing changes except the removals.
    """
    kept_ids = {c.get("id") for c in cleaned if c.get("id")}
    result = []
    for r in original:
        if _is_criterion(r) and r.get("id") not in kept_ids:
            continue
        result.append(r)
    return result


def process_criterion_cleanup(input_json_dir: str, output_file_name: str = "criterion_cleanup", output_dir: str = "outputs") -> str:

    print(f"Processing criterion cleanup: {input_json_dir}")
    original = _as_requirements_list(extract_json_from_file(input_json_dir))

    # Send ONLY the criteria (id + description) plus each one's related requirement as context.
    reduced_criteria = _reduce_criteria_with_related(original)
    if reduced_criteria:
        reduced_json = json.dumps({"requirements": reduced_criteria}, ensure_ascii=False, indent=2)
        cleanup_prompt = build_requirements_json_prompt(Prompts.CRITERION_CLEANUP_PROMPT, reduced_json)

        cleanup_response = ask_gemini(
            user_prompt=cleanup_prompt
        )

        # The model returns the surviving criteria; merge back onto the original file
        # so only the removed criteria are gone and every other field stays untouched.
        cleaned_items = _as_requirements_list(extract_json_from_response(cleanup_response))
        cleaned = filter_cleaned_criteria(original, cleaned_items)
    else:
        cleaned = original  # no criteria to review

    output_path = save_json(cleaned, output_file_name, output_dir)
    print(f"Criterion cleanup saved successfully for: {input_json_dir}")
    return str(output_path)

def process_validation_prompt(file: str, input_json_dir: str, prompt:str, output_file_name: str, output_dir: str = "outputs") -> None:

    print(f"Processing validation prompt: {file}")
    input_json =  extract_json_from_file(input_json_dir)
    validation_prompt = build_validation_prompt(file, prompt, input_json)

    validation_response = ask_gemini(
        user_prompt=validation_prompt
    )
    save_result(output_file_name, output_dir, validation_response)
    print(f"Validation saved successfully for: {input_json_dir}")






def save_result(file: str, output_dir: str, response: str):
    data = extract_json_from_response(response)
    output_path = save_json(data, file, output_dir)
    return output_path