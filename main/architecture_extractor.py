from resource.prompts.prompts import Prompts
import os
from pathlib import Path
from dotenv import load_dotenv
from service.prompt_service import process_single_prompt, merge_architecture
from service.architecture_evaluator_service import evaluate_architecture
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
                folder = ("../resource/docs/" + file_name).strip()
                file = folder + "/" + file_name + ".pdf"

                # Architecture is extracted with two independent prompts (run
                # separately) whose results are merged in the program. Images in
                # `folder` are sent along with the text so the model can read the
                # architecture diagrams. The two prompts use disjoint id namespaces
                # (AU_xx for units, P_xx for patterns) and no cross-group part-of.
                architecture_output_dir = "../outputs/gemini/architecture/" + file_name

                # Pass A — Architectural Units (Layer, Component, Service, Device,
                # Connector, Technology, Other).
                units_json_path = process_single_prompt(
                    file=file,
                    folder=folder,
                    prompt=Prompts.ARCHITECTURAL_UNIT_EXTRACTION_PROMPT,
                    output_dir=architecture_output_dir,
                    output_file_name=file_name + "_units"
                )
                print(f"Architectural units saved: {units_json_path}")

                # Pass B — Patterns (Architectural Pattern, Design Pattern).
                patterns_json_path = process_single_prompt(
                    file=file,
                    folder=folder,
                    prompt=Prompts.PATTERN_EXTRACTION_PROMPT,
                    output_dir=architecture_output_dir,
                    output_file_name=file_name + "_patterns"
                )
                print(f"Patterns saved: {patterns_json_path}")

                # Merge the two results into the canonical architecture file that
                # the evaluation consumes.
                llm_json_path = merge_architecture(
                    units_json_dir=units_json_path,
                    patterns_json_dir=patterns_json_path,
                    output_file_name=file_name + "_architecture",
                    output_dir=architecture_output_dir
                )
                print(f"Architecture (units + patterns) merged: {llm_json_path}")

                # Evaluate against the architecture ground truth if available.
                gt_path = f"../resource/groundTruths/architecture/{file_name}_ground_truth_architecture.xlsx"
                if Path(gt_path).exists():
                    eval_output_path = f"../outputs/evaluation/architecture/{file_name}_arch_eval.xlsx"
                    evaluate_architecture(gt_path, llm_json_path, eval_output_path)
                    print(f"Architecture evaluation saved: {eval_output_path}")
                else:
                    print(f"No architecture ground truth found for '{file_name}', skipping evaluation.")
            except Exception as file_error:
                print(f"Error while processing '{file_name}': {file_error}")

    except Exception as e:
        print(f"Startup error: {e}")
