import json
import unittest
from unittest.mock import patch

from src.product_intelligence.source_discovery import (
    SerperSearchProvider,
)


class SerperTransportBoundaryTests(unittest.TestCase):
    def test_default_transport_uses_configured_serper_request(self) -> None:
        captured = {}

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout

            class Response:
                status = 200

                def read(self):
                    return b'{"organic": [{"title": "Candidate", "link": "https://example.com/p", "snippet": "snippet"}]}'

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        secret = "boundary-test-secret"
        with patch("src.product_intelligence.source_discovery.urlopen", fake_urlopen):
            results = SerperSearchProvider(api_key=secret).search("576512 Philips", 5)

        request = captured["request"]
        self.assertEqual(request.full_url, "https://google.serper.dev/search")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data)["q"], "576512 Philips")
        self.assertEqual(json.loads(request.data)["num"], 5)
        self.assertTrue(any(key.casefold() == "x-api-key" for key in request.headers))
        self.assertNotIn(secret.encode(), request.data)
        self.assertEqual(captured["timeout"], 15.0)
        self.assertEqual(results[0].url, "https://example.com/p")


if __name__ == "__main__":
    unittest.main()
