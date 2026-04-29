import os
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()


def get_dashscope_api_key() -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise ValueError("DASHSCOPE_API_KEY is missing.")
    return api_key


def create_qwen_client() -> OpenAI:
    return OpenAI(
        api_key=get_dashscope_api_key(),
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )


def ask_qwen(
        user_prompt: str,
        system_prompt: str = "You are a helpful assistant.",
        model: str = "qwen-vl-plus-latest",
) -> str:
    client = create_qwen_client()

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )

    return completion.choices[0].message.content




