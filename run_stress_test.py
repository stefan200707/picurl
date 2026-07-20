import re

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.api.endpoints import get_http_client


def mock_validator_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 47})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


app.dependency_overrides[get_http_client] = mock_validator_client
client = TestClient(app)


def main():
    with open("stress_test_queries.md", encoding="utf-8") as f:
        content = f.read()

    sections = re.split(r"## \d+\. ", content)
    queries = []
    for section in sections[1:]:
        match = re.search(r'> "(.*?)"', section, re.DOTALL)
        if match:
            query = match.group(1).strip()
            title = section.split("\n")[0].strip()
            queries.append((title, query))

    for i, (title, text) in enumerate(queries, 1):
        print(f"\n{'=' * 80}\nQuery {i}: {title}\nText: {text}")
        response = client.post("/build-url", json={"text": text})
        if response.status_code != 200:
            print(f"ERROR: Status {response.status_code}, {response.text}")
            continue
        data = response.json()
        print(f"URL: {data['url']}")
        print(f"Criteria: {data['criteria']}")
        print(f"Warnings: {data['warnings']}")


if __name__ == "__main__":
    main()
