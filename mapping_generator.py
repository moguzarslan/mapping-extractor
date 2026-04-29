from resource.prompts.prompts import Prompts
import os
from dotenv import load_dotenv
from service.prompt_service import process_single_prompt, process_chained_prompt
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
        value = os.getenv("PROMPT_CHAINING").strip()

        for file_name in file_names:

            try:
                folder = ("resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"
                process_chained_prompt(
                    file=file,
                    folder=folder,
                    final_prompt=Prompts.CHAINED_MAPPING_EXTRACTION_PROMPT,
                    output_dir="outputs/chained/"+file_name
                )

                process_single_prompt(
                    file=file,
                    folder=folder,
                    prompt=Prompts.MAPPING_EXTRACTION_PROMPT,
                    output_dir="outputs/single"
                )
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
