"""Text requests execute the same application handlers as structured requests."""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from schedule_api.dependencies import get_nlp_processor
from schedule_nlp.llm_provider import LLMProvider
from schedule_nlp.nlp_processor import NLPProcessor

from ..conftest import build_rpc_request


@pytest.fixture
def model_responses(test_app):
    provider = AsyncMock(spec=LLMProvider)
    processor = NLPProcessor(provider, None)
    test_app.dependency_overrides[get_nlp_processor] = lambda: processor
    yield provider._generate_json
    test_app.dependency_overrides.pop(get_nlp_processor)


def send(client, headers, model, intent, payload):
    model.return_value = {"intent": intent, "payload": payload}
    task_id = str(uuid4())
    response = client.post(
        "/api/rpc/",
        headers=headers,
        json=build_rpc_request(
            "tasks/send",
            {
                "id": task_id,
                "message": {
                    "role": "user",
                    "parts": [{"type": "text", "text": "Scheduling request"}],
                },
            },
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body.get("error") is None, body
    assert body["result"]["id"] == task_id
    return body["result"]


def test_text_event_lifecycle(client, auth_headers_valid, model_responses):
    def call(intent, payload):
        result = send(client, auth_headers_valid, model_responses, intent, payload)
        assert result["status"]["state"] == "completed", result
        return result

    created = call(
        "create_event",
        {
            "title": "Lunch",
            "start_time": "2030-01-07T12:00:00Z",
            "end_time": "2030-01-07T13:00:00Z",
            "time_zone": "UTC",
        },
    )
    identity = created["artifacts"][0]["parts"][0]["data"]["id"]
    call("update_event", {"id": identity, "title": "Updated lunch"})
    query = {"start_time": "2030-01-07T00:00:00Z", "end_time": "2030-01-08T00:00:00Z"}
    rows = json.loads(call("query_events", query)["status"]["message"]["parts"][0]["text"])
    assert [(r["id"], r["title"]) for r in rows] == [(identity, "Updated lunch")]
    exported = call(
        "export_calendar",
        {"format": "ics", "date_info": {"start_date": "2030-01-07", "end_date": "2030-01-08"}},
    )
    assert exported["artifacts"][0]["parts"][0]["file"]["bytes"]
    call("delete_event", {"id": identity})
    assert json.loads(call("query_events", query)["status"]["message"]["parts"][0]["text"]) == []


@pytest.mark.parametrize(
    ("intent", "payload", "state"),
    [
        ("clarify", {"message": "When should it start?"}, "input-required"),
        ("unknown", {}, "failed"),
    ],
)
def test_text_requires_input_or_reports_failure(
    client, auth_headers_valid, model_responses, intent, payload, state
):
    result = send(client, auth_headers_valid, model_responses, intent, payload)
    assert result["status"]["state"] == state
    assert not result["artifacts"]
    if intent == "clarify":
        assert result["status"]["message"]["parts"][0]["text"] == payload["message"]


def test_clarification_followup_preserves_conversation(
    client, test_app, auth_headers_valid, model_responses
):
    identity = str(uuid4())
    initial = "Create Standup on January 7 2030 at 09:00 UTC."
    question = "How long should it last?"

    def post(text):
        response = client.post(
            "/api/rpc/",
            headers=auth_headers_valid,
            json=build_rpc_request(
                "tasks/send",
                {
                    "id": identity,
                    "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
                },
            ),
        )
        body = response.json()
        assert body.get("error") is None, body
        return body["result"]

    model_responses.return_value = {"intent": "clarify", "payload": {"message": question}}
    assert post(initial)["status"]["state"] == "input-required"
    model_responses.return_value = {
        "intent": "create_event",
        "payload": {
            "title": "Standup",
            "start_time": "2030-01-07T09:00:00Z",
            "end_time": "2030-01-07T10:00:00Z",
            "time_zone": "UTC",
        },
    }
    completed = post("One hour.")
    prompt, query = model_responses.await_args.args
    assert initial in query and question in query and "One hour." in query
    assert completed["status"]["state"] == "completed"
    assert [m["parts"][0]["text"] for m in completed["history"][:3]] == [
        initial,
        question,
        "One hour.",
    ]
