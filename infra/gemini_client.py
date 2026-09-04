import os
from datetime import datetime, timezone
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


DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"


def get_gemini_model() -> str:
    """The generation model, configurable through GEMINI_MODEL in .env."""
    return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)


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
        model: str | None = None,
) -> str:
    client = create_gemini_client()
    model = model or get_gemini_model()

    timestamp = datetime.now(timezone.utc).isoformat()
    timestamped_prompt = f"[{timestamp}]\n{user_prompt}"

    response = client.models.generate_content(
        model=model,
        contents=timestamped_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0,
        ),
    )

    return response.text