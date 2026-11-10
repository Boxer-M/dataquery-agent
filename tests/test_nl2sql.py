import unittest
from unittest.mock import Mock

from dataquery_agent.nl2sql import (
    SqlGenerationError,
    SqlValidationError,
    answer_question,
)
from dataquery_agent.sql_executor import QueryResult


QUESTION = "全部状态、不限时间的订单记录数是多少？"
SCHEMA_CONTEXT = "表：public.ods_orders\n字段：\n- order_id: text"
COUNT_SQL = "SELECT COUNT(*) FROM public.ods_orders;"


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_reader = Mock(return_value=SCHEMA_CONTEXT)
        self.prompt_builder = Mock(return_value="test prompt")
        self.sql_generator = Mock(return_value=COUNT_SQL)
        self.executor = Mock(
            return_value=QueryResult(
                columns=("count",),
                rows=((99441,),),
                truncated=False,
            )
        )

    def call_answer_question(self):
        return answer_question(
            QUESTION,
            schema_reader=self.schema_reader,
            prompt_builder=self.prompt_builder,
            sql_generator=self.sql_generator,
            executor=self.executor,
        )

    def test_returns_sql_and_query_result(self) -> None:
        result = self.call_answer_question()

        self.prompt_builder.assert_called_once_with(QUESTION, SCHEMA_CONTEXT)
        self.sql_generator.assert_called_once_with("test prompt")
        self.executor.assert_called_once_with(COUNT_SQL)
        self.assertEqual(result.sql, COUNT_SQL)
        self.assertEqual(result.columns, ("count",))
        self.assertEqual(result.rows, ((99441,),))
        self.assertFalse(result.truncated)

    def test_guard_rejection_does_not_execute_sql(self) -> None:
        self.sql_generator.return_value = "DELETE FROM public.ods_orders;"

        with self.assertRaisesRegex(SqlValidationError, "只允许 SELECT"):
            self.call_answer_question()

        self.executor.assert_not_called()

    def test_rejects_empty_model_output(self) -> None:
        for model_text in (None, "", "   "):
            with self.subTest(model_text=model_text):
                self.sql_generator.return_value = model_text
                with self.assertRaisesRegex(SqlGenerationError, "未返回 SQL"):
                    self.call_answer_question()

        self.executor.assert_not_called()

    def test_rejects_multiple_statements(self) -> None:
        self.sql_generator.return_value = (
            "SELECT COUNT(*) FROM public.ods_orders; "
            "SELECT COUNT(*) FROM public.ods_customers;"
        )

        with self.assertRaisesRegex(SqlValidationError, "只允许一条"):
            self.call_answer_question()

        self.executor.assert_not_called()

    def test_generation_failure_does_not_execute_sql(self) -> None:
        self.sql_generator.side_effect = RuntimeError("private API detail")

        with self.assertRaisesRegex(SqlGenerationError, "Gemini SQL 生成失败") as caught:
            self.call_answer_question()

        self.assertNotIn("private API detail", str(caught.exception))
        self.executor.assert_not_called()

    def test_rejects_markdown_code_block(self) -> None:
        self.sql_generator.return_value = f"```sql\n{COUNT_SQL}\n```"

        with self.assertRaisesRegex(SqlGenerationError, "Markdown"):
            self.call_answer_question()

        self.executor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
