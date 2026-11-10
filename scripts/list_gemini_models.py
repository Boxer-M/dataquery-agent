import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
    for model in client.models.list():
        if "flash" in (model.name or "").lower():
            print(model.name)
            print(f"支持操作：{model.supported_actions}")
