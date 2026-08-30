"""Health and the local debug outbox."""

from __future__ import annotations

from tests.conftest import PHONE


class TestHealth:
    async def test_reports_the_active_providers(self, client):
        body = (await client.get("/health")).json()

        assert body == {
            "status": "ok",
            "smsProvider": "mock",
            "llmProvider": "mock",
            "replyMode": "api",
        }

    async def test_needs_no_authentication(self, client):
        assert (await client.get("/health")).status_code == 200


class TestDebugOutbox:
    async def test_lists_what_the_mock_provider_sent(self, client):
        await client.post(
            "/webhooks/sms",
            json={"from": PHONE, "body": "How do I reset my password?", "messageId": "SM1"},
        )

        outbox = (await client.get("/debug/outbox")).json()

        assert len(outbox) == 1
        assert outbox[0]["to"] == PHONE
        assert "Forgot password" in outbox[0]["body"]
        assert outbox[0]["messageId"].startswith("MOCK")
        assert outbox[0]["sentAt"]

    async def test_is_empty_before_anything_is_sent(self, client):
        assert (await client.get("/debug/outbox")).json() == []

    async def test_can_be_switched_off(self, app_factory):
        async with app_factory(debug_endpoints_enabled=False) as (client, _):
            assert (await client.get("/debug/outbox")).status_code == 404

    async def test_is_unavailable_for_a_real_provider(self, app_factory):
        async with app_factory(
            sms_provider="twilio",
            twilio_account_sid="AC1",
            twilio_auth_token="token",
            twilio_phone_number="+15550001111",
        ) as (client, _):
            response = await client.get("/debug/outbox")

            assert response.status_code == 404
            assert response.json()["error"]["code"] == "not_found"


class TestOpenApi:
    async def test_the_schema_documents_every_route(self, client):
        paths = (await client.get("/openapi.json")).json()["paths"]

        assert set(paths) == {
            "/health",
            "/webhooks/sms",
            "/webhooks/sms/twilio",
            "/admin/conversations",
            "/debug/outbox",
        }
