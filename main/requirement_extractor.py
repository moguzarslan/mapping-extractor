from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import process_single_prompt, process_chained_prompt, process_requirement_splitting, process_criterion_cleanup
from service.evaluator_service import evaluate
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
                # process_chained_prompt(
                #     file=file,
                #     folder=folder,
                #     final_prompt=Prompts.CHAINED_MAPPING_EXTRACTION_PROMPT,
                #     output_dir="outputs/chained/"+file_name
                # )

                requirements_output_dir = "outputs/gemini/" + file_name
                llm_json_path = process_single_prompt(
                    file=file,
                    folder=folder,
                    prompt=Prompts.REQUIREMENT_EXTRACTION_PROMPT,
                    output_dir=requirements_output_dir
                )

                # Split compound requirements (model sees only id + description),
                # then copy the remaining fields back onto the split ids.
                split_json_path = process_requirement_splitting(
                    input_json_dir=llm_json_path,
                    output_file_name=file_name + "_split",
                    output_dir=requirements_output_dir
                )

                # Remove low-value acceptance criteria from the split output.
                cleanup_json_path = process_criterion_cleanup(
                    input_json_dir=split_json_path,
                    output_file_name=file_name + "_criterion_cleanup",
                    output_dir=requirements_output_dir
                )

                gt_path = f"resource/groundTruths/requirement/{file_name}_ground_truth.xlsx"
                if Path(gt_path).exists():
                    eval_output_path = f"outputs/evaluation/requirement/{file_name}_req_eval.xlsx"
                    evaluate(gt_path, cleanup_json_path, eval_output_path)
                    print(f"Evaluation saved: {eval_output_path}")
                else:
                    print(f"No ground truth found for '{file_name}', skipping evaluation.")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
