import os
from platform import architecture

from dotenv import load_dotenv

from resource.prompts.prompts import Prompts
from service.prompt_service import process_validation_prompt

load_dotenv()


def get_document_files_from_env(env_key: str = "DOCUMENT_FILES") -> list[str]:
    value = os.getenv(env_key, "").strip()
    if not value:
        raise ValueError(f"Environment variable '{env_key}' is empty or not set.")

    files = [item.strip() for item in value.split(",") if item.strip()]
    if not files:
        raise ValueError(f"No valid file paths found in '{env_key}'.")

    return files

if __name__ == "__main__":
    try:
        file_names = get_document_files_from_env("DOCUMENTS")
        for file_name in file_names:
            try:
                folder = ("resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"
                output_dir = "resource/outputs/validation/" + file_name
                json_dir = "resource/outputs/qwen3-5/chained-01/" + file_name

                process_validation_prompt(
                    file=file,
                    input_json_dir=json_dir + "/requirement.json",
                    output_dir=output_dir,
                    output_file_name="requirement",
                    prompt=Prompts.REQUIREMENT_VALIDATION_PROMPT
                )

                process_validation_prompt(
                    file=file,
                    input_json_dir=json_dir + "/architecture.json",
                    output_dir=output_dir,
                    output_file_name="architecture",
                    prompt=Prompts.ARCHITECTURE_VALIDATION_PROMPT
                )

                process_validation_prompt(
                    file=file,
                    input_json_dir=json_dir + "/mapping.json",
                    output_dir=output_dir,
                    output_file_name="mapping",
                    prompt=Prompts.MAPPING_VALIDATION_PROMPT
                )

            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")


