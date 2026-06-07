
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
def process_single_prompt(file: str, folder: str, prompt: str, output_dir: str = "outputs") -> str:
    print(f"Processing: {folder}")
    prompt = build_document_prompt(file, prompt, image_folder=folder)
    response = ask_gemini(
        user_prompt=prompt,
    )
    output_path = save_result(
        file=file,
        output_dir=output_dir,
        response=response
    )
    print("Single prompt completed, results are saved successfully")
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


def _reduce_to_id_description(requirements: list) -> list:
    """Keep only id and description for each requirement (the splitting input)."""
    return [{"id": r.get("id"), "description": r.get("description")} for r in requirements]


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


def merge_split_requirements(original: list, split: list) -> list:
    """Re-attach the non-text fields onto the split requirements.

    The splitting step only returns id + description. For each returned element
    we copy every other field (type, pageNumber, concept, categorization,
    relatedTo, fixes, ...) from its parent requirement, then overwrite id and
    description with the split values.
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
        merged.append(record)
    return merged


def process_requirement_splitting(input_json_dir: str, output_file_name: str = "requirement_split", output_dir: str = "outputs") -> str:

    print(f"Processing requirement splitting: {input_json_dir}")
    original = _as_requirements_list(extract_json_from_file(input_json_dir))

    # Send ONLY id + description to the model.
    reduced = _reduce_to_id_description(original)
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