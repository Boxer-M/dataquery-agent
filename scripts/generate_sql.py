import os
from pathlib import Path

from dotenv import load_dotenv
from google import genai

from dataquery_agent.nl2sql import answer_question
from preview_prompt import build_prompt
from read_schema import get_ods_schema


def generate_sql(prompt: str) -> str | None:
    with genai.Client(api_key=os.environ["GEMINI_API_KEY"]) as client:
        response = client.models.generate_content(
            model=os.environ["GEMINI_MODEL"],
            contents=prompt,
        )
    return response.text


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    question = "数据集中共有多少条订单记录？包含所有状态，不限定时间。"
    result = answer_question(
        question,
        schema_reader=get_ods_schema,
        prompt_builder=build_prompt,
        sql_generator=generate_sql,
    )
    print(f"SQL：{result.sql}")
    print(f"列名：{result.columns}")
    print(f"结果：{result.rows}")
    print(f"是否截断：{result.truncated}")
