from __future__ import annotations

import unittest

from automation import opencode_structured_output


class OpenCodeStructuredOutputTests(unittest.TestCase):
    def test_success_reads_documented_structured_output_field(self):
        result = opencode_structured_output.parse_prompt_response(
            {
                "info": {
                    "structured_output": {
                        "verdict": "pass",
                        "requirements": [],
                        "findings": [],
                        "repair_brief": "",
                    }
                },
                "parts": [],
            }
        )
        self.assertEqual(result.value["verdict"], "pass")
        self.assertEqual(result.retries, 0)

    def test_schema_exhaustion_is_distinct_from_transport_failure(self):
        with self.assertRaises(opencode_structured_output.StructuredOutputExhausted) as raised:
            opencode_structured_output.parse_prompt_response(
                {
                    "info": {
                        "error": {
                            "name": "StructuredOutputError",
                            "message": "schema validation failed",
                            "retries": 2,
                        }
                    }
                }
            )
        self.assertEqual(raised.exception.retries, 2)

    def test_missing_structured_output_is_explicitly_unsupported(self):
        with self.assertRaises(opencode_structured_output.StructuredOutputUnavailable):
            opencode_structured_output.parse_prompt_response(
                {"info": {"id": "message-1"}, "parts": [{"type": "text", "text": "free form"}]}
            )

    def test_other_message_error_is_runtime_transport_failure(self):
        with self.assertRaises(opencode_structured_output.StructuredOutputTransportError):
            opencode_structured_output.parse_prompt_response(
                {
                    "info": {
                        "error": {
                            "name": "ProviderAuthError",
                            "message": "provider rejected request",
                        }
                    }
                }
            )


if __name__ == "__main__":
    unittest.main()
