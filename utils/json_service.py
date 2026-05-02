import json
import re
from pathlib import Path


def extract_json_from_response(response: str):
    """
    Tries to parse response directly as JSON.
    If that fails, tries to extract the first JSON object or array from the text.
    """
    if isinstance(response, dict) or isinstance(response, list):
        return response

    if not isinstance(response, str):
        raise ValueError("Response is neither a JSON string nor a Python dict/list.")

    response = response.strip()

    # First try direct parse
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Remove markdown code fences if present
    response = re.sub(r"^```json\s*", "", response, flags=re.IGNORECASE)
    response = re.sub(r"^```\s*", "", response)
    response = re.sub(r"\s*```$", "", response)

    # Try extracting JSON object
    object_match = re.search(r"\{.*\}", response, re.DOTALL)
    if object_match:
        try:
            return json.loads(object_match.group())
        except json.JSONDecodeError:
            pass

    # Try extracting JSON array
    array_match = re.search(r"\[.*\]", response, re.DOTALL)
    if array_match:
        try:
            return json.loads(array_match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON could be parsed from the model response.")

def extract_json_from_file (file):
    with open(file, "r") as f:
        return json.load(f)

def save_json(response:str,file_path: str, output_dir: str = "outputs") -> Path:
    data = extract_json_from_response(response)
    input_path = Path(file_path)
    output_folder = Path(output_dir)
    output_folder.mkdir(parents=True, exist_ok=True)
    output_path = output_folder / f"{input_path.stem}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


