import unittest

from src.product_intelligence.gemini_client import (
    GeminiClient,
    GeminiProviderError,
    GeminiTransientError,
    _is_transient_503,
)


class FakeResponse:
    def __init__(self, text: str = "OK") -> None:
        self.text = text


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class FakeSDK:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


def client_for(outcomes, sleeps):
    client = object.__new__(GeminiClient)
    client.model = "test-model"
    client._client = FakeSDK(outcomes)
    client._sleep = sleeps.append
    return client


class GeminiRetryTests(unittest.TestCase):
    def test_transient_503_retries_with_bounded_backoff_then_succeeds(self):
        sleeps = []
        client = client_for(
            [RuntimeError("503 UNAVAILABLE"), RuntimeError("503 UNAVAILABLE"), FakeResponse()],
            sleeps,
        )

        result = client.generate_text("hello")

        self.assertEqual(result, "OK")
        self.assertEqual(sleeps, [1.0, 4.0])
        self.assertEqual(client._client.models.calls, 3)

    def test_three_retries_are_attempted_then_transient_failure_is_recorded(self):
        sleeps = []
        client = client_for([RuntimeError("503 UNAVAILABLE")] * 4, sleeps)

        with self.assertRaises(GeminiTransientError) as context:
            client.generate_text("hello")

        self.assertEqual(context.exception.attempts, 4)
        self.assertEqual(sleeps, [1.0, 4.0, 8.0])
        self.assertEqual(client._client.models.calls, 4)
        self.assertIn("transient failure", str(context.exception))
        self.assertEqual(context.exception.provider_category, "HTTP_503")
        self.assertNotIn("503 UNAVAILABLE", str(context.exception))

    def test_non_503_errors_are_not_retried(self):
        sleeps = []
        client = client_for([RuntimeError("401 UNAUTHENTICATED")], sleeps)

        with self.assertRaises(GeminiProviderError) as context:
            client.generate_text("hello")

        self.assertEqual(sleeps, [])
        self.assertEqual(client._client.models.calls, 1)
        self.assertEqual(context.exception.provider_category, "API_ERROR")
        self.assertNotIn("401", str(context.exception))

    def test_provider_failure_categories_are_bounded_and_safe(self):
        class StatusError(Exception):
            def __init__(self, status_code):
                self.status_code = status_code

        cases = [
            (StatusError(429), "HTTP_429"),
            (TimeoutError("secret timeout details"), "TIMEOUT"),
            (ConnectionError("secret connection details"), "CONNECTION_ERROR"),
            (RuntimeError("secret provider response"), "API_ERROR"),
        ]
        for failure, expected_category in cases:
            with self.subTest(expected_category=expected_category):
                client = client_for([failure], [])
                with self.assertRaises(GeminiProviderError) as context:
                    client.generate_structured_json("hello", dict)  # type: ignore[arg-type]
                self.assertEqual(context.exception.provider_category, expected_category)
                self.assertNotIn("secret", str(context.exception))

    def test_structured_requests_use_the_same_retry_behavior(self):
        sleeps = []
        client = client_for([RuntimeError("503 UNAVAILABLE"), FakeResponse('{"ok":true}')], sleeps)

        result = client.generate_structured_json("hello", dict)  # type: ignore[arg-type]

        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(sleeps, [1.0])

    def test_transient_detection_is_narrow(self):
        self.assertTrue(_is_transient_503(RuntimeError("503 UNAVAILABLE")))
        self.assertFalse(_is_transient_503(RuntimeError("429 RESOURCE_EXHAUSTED")))
        self.assertFalse(_is_transient_503(RuntimeError("401 UNAUTHENTICATED")))


if __name__ == "__main__":
    unittest.main()
