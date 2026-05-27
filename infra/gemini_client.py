import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()


def get_gemini_api_key() -> str:
    """
    Kept only to preserve the method name.
    Vertex AI does not use GEMINI_API_KEY.
    It uses ADC instead:
        gcloud auth application-default login
    """
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project_id:
        raise ValueError("GOOGLE_CLOUD_PROJECT is missing.")
    return project_id


def create_gemini_client() -> genai.Client:
    project_id = get_gemini_api_key()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
    )


def ask_gemini(
        user_prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        model: str = "gemini-2.5-flash",
) -> str:
    client = create_gemini_client()

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
        ),
    )

    return response.text