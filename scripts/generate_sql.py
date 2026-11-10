import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from preview_prompt import build_prompt
from read_schema import get_order_schema


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    question = "数据集中共有多少条订单记录？包含所有状态，不限定时间。"
    schema_context = get_order_schema()
    prompt = build_prompt(question, schema_context)

    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        response = client.models.generate_content(
            model=os.environ["GEMINI_MODEL"],
            contents=prompt,
        )

    sql_text = response.text
    if not sql_text or not sql_text.strip():
        raise SystemExit("模型未返回文本，请检查响应状态")
    print(sql_text)
