from read_schema import get_ods_schema


def build_prompt(question: str, schema_context: str) -> str:
    return f"""你负责根据问题生成 PostgreSQL 查询。
规则：
- 仅使用以下表结构中提供的表和字段。
- 物理表必须显式使用 ods.表名，不依赖 search_path。
- 只输出一条 SELECT 查询，不输出解释或 Markdown。
- 信息不足时说明缺少的信息，不编造字段。

表结构：
{schema_context}

用户问题：
{question}
"""


if __name__ == "__main__":
    question = "数据集中共有多少条订单记录？包含所有状态，不限定时间。"
    schema_context = get_ods_schema()
    prompt = build_prompt(question, schema_context)
    print(prompt)
