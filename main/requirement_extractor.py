from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import extract_requirements, process_chained_prompt, split_requirements, cleanup_criteria, extract_concepts, save_requirements
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

                requirements = extract_requirements(
                    file=file,
                    folder=folder,
                    prompt=Prompts.REQUIREMENT_EXTRACTION_PROMPT,
                )

                # Split compound requirements (model sees only id + description),
                # then copy the remaining fields back onto the split ids.
                requirements = split_requirements(requirements)

                # Remove low-value acceptance criteria from the split output.
                requirements = cleanup_criteria(requirements)

                # Evaluate against the ground truth BEFORE the concept-id rewrite
                # below, so the evaluator still compares the raw concept text.
                gt_path = f"resource/groundTruths/requirement/{file_name}_ground_truth.xlsx"
                if Path(gt_path).exists():
                    eval_output_path = f"outputs/evaluation/requirement/{file_name}_req_eval.xlsx"
                    evaluate(gt_path, requirements, eval_output_path)
                    print(f"Evaluation saved: {eval_output_path}")
                else:
                    print(f"No ground truth found for '{file_name}', skipping evaluation.")

                # Concept extraction is purely programmatic (no model call) and runs
                # after evaluation, so it never affects the evaluation. Its output,
                # written here, is the ONLY pair of files the requirements pipeline
                # persists: <file_name>_requirements.json and <file_name>_concepts.json.
                requirements, concepts = extract_concepts(requirements)
                requirements_json_path, concepts_json_path = save_requirements(
                    requirements, concepts,
                    output_file_name=file_name,
                    output_dir=requirements_output_dir
                )
                print(f"Requirements saved: {requirements_json_path}")
                print(f"Concepts saved: {concepts_json_path}")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
