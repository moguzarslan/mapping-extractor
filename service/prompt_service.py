from typing import Union

from infra.document_service import read_document
from utils.image_transformer import ImageTransformer
from utils.json_service import extract_json_from_response, save_json, extract_json_from_file
from resource.prompts.prompts import Prompts
from infra.qwen_client import ask_qwen


def build_document_prompt(
        file: str,
        prompt: str,
        image_folder: str,
) -> Union[str, list]:
    """
    Builds:
    - text-only prompt (string)
    - or multimodal prompt (list with text + images)
    """
    document_text = read_document(file)
    full_text = f"""
{prompt}

Document:
\"\"\"
{document_text}
\"\"\"
""".strip()

    # If images exist → return multimodal content
    content = [{"type": "text", "text": full_text}]

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
def process_single_prompt(file: str, folder: str, prompt: str, output_dir: str = "outputs") -> None:
    print(f"Processing: {folder}")
    prompt = build_document_prompt(file, prompt, image_folder="")
    response = ask_qwen(
        user_prompt=prompt,
    )
    save_result(
        file=file,
        output_dir=output_dir,
        response=response
    )
    print("Single prompt completed, results are saved successfully")


def process_chained_prompt(file: str, folder: str, final_prompt: str, output_dir: str = "outputs") -> None:

    print(f"Processing: {folder}")
    requirements_prompt = build_document_prompt(file, Prompts.REQUIREMENT_EXTRACTION_PROMPT, None)
    requirements_response = ask_qwen(
        user_prompt=requirements_prompt
    )
    save_result('requirement', output_dir, requirements_response)
    print("Requirements saved successfully")

    architecture_prompt = build_document_prompt(file, Prompts.ARCHITECTURE_EXTRACTION_PROMPT, image_folder=folder)
    architecture_response = ask_qwen(
        user_prompt=architecture_prompt
    )
    save_result('architecture', output_dir, architecture_response)
    print("Architectural items saved successfully")

    mapping_prompt = build_chained_json_prompt(
        prompt=final_prompt,
        architecture_json=architecture_response,
        requirements_json=requirements_response, )

    response = ask_qwen(
        user_prompt=mapping_prompt,
    )
    save_result('mapping', output_dir, response)
    print("Chained prompt completed, results are saved successfully")

def process_validation_prompt(file: str, input_json_dir: str, prompt:str, output_file_name: str, output_dir: str = "outputs") -> None:

    print(f"Processing validation prompt: {file}")
    input_json =  extract_json_from_file(input_json_dir)
    validation_prompt = build_validation_prompt(file, prompt, input_json)

    validation_response = ask_qwen(
        user_prompt=validation_prompt
    )
    save_result(output_file_name, output_dir, validation_response)
    print(f"Validation saved successfully for: {input_json_dir}")






def save_result(file: str, output_dir: str, response: str):
    data = extract_json_from_response(response)
    output_path = save_json(data, file, output_dir)
    return output_path