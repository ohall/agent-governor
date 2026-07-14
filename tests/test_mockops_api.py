from __future__ import annotations

from fastapi.testclient import TestClient


class TestIncidents:
    def test_list_returns_all_seeded_incidents(self, client: TestClient) -> None:
        response = client.get("/incidents")
        assert response.status_code == 200
        incidents = response.json()
        assert len(incidents) == 12
        assert incidents[0]["id"] == "INC-1001"

    def test_list_filters_by_state_and_service(self, client: TestClient) -> None:
        by_state = client.get("/incidents", params={"state": "resolved"}).json()
        assert [i["id"] for i in by_state] == ["INC-1012"]
        by_service = client.get("/incidents", params={"service_id": "SVC-001"}).json()
        assert {i["service_id"] for i in by_service} == {"SVC-001"}

    def test_get_unknown_incident_404s(self, client: TestClient) -> None:
        assert client.get("/incidents/INC-9999").status_code == 404

    def test_patch_applies_partial_update(self, client: TestClient) -> None:
        response = client.patch(
            "/incidents/INC-1001",
            json={"state": "investigating", "assigned_to": "maria.chen"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "investigating"
        assert body["assigned_to"] == "maria.chen"
        assert body["severity"] == "critical"  # untouched field preserved

    def test_patch_with_no_fields_422s(self, client: TestClient) -> None:
        assert client.patch("/incidents/INC-1001", json={}).status_code == 422

    def test_resolve_sets_state_and_note(self, client: TestClient) -> None:
        response = client.post(
            "/incidents/INC-1001/resolve",
            json={"resolution_note": "Rolled back payments-api to 4.12.0."},
        )
        assert response.status_code == 200
        assert response.json()["state"] == "resolved"
        assert "Rolled back" in response.json()["resolution_note"]

    def test_resolve_twice_conflicts(self, client: TestClient) -> None:
        client.post("/incidents/INC-1001/resolve", json={"resolution_note": "done"})
        second = client.post("/incidents/INC-1001/resolve", json={"resolution_note": "again"})
        assert second.status_code == 409


class TestCmdb:
    def test_list_services(self, client: TestClient) -> None:
        services = client.get("/cmdb/services").json()
        assert len(services) == 8
        assert services[0]["id"] == "SVC-001"

    def test_get_service_with_dependencies(self, client: TestClient) -> None:
        service = client.get("/cmdb/services/SVC-001").json()
        assert service["name"] == "payments-api"
        assert service["dependencies"] == ["SVC-004", "SVC-008"]

    def test_unknown_service_404s(self, client: TestClient) -> None:
        assert client.get("/cmdb/services/SVC-999").status_code == 404


class TestRiskyOperations:
    def test_deploy_updates_service_version(self, client: TestClient) -> None:
        result = client.post("/deploy", json={"service_id": "SVC-002", "version": "9.3.1"}).json()
        assert result["previous_version"] == "9.3.0"
        assert result["deployed_version"] == "9.3.1"
        assert client.get("/cmdb/services/SVC-002").json()["version"] == "9.3.1"

    def test_notify_queues_notification(self, client: TestClient) -> None:
        result = client.post(
            "/notify",
            json={"channel": "slack", "recipient": "#inc-1001", "message": "mitigated"},
        ).json()
        assert result["notification_id"].startswith("NTF-")

    def test_notify_rejects_unknown_channel(self, client: TestClient) -> None:
        response = client.post("/notify", json={"channel": "fax", "recipient": "x", "message": "y"})
        assert response.status_code == 422

    def test_grant_and_revoke_privileges(self, client: TestClient) -> None:
        granted = client.post(
            "/users/USR-02/privileges", json={"role": "admin", "action": "grant"}
        ).json()
        assert "admin" in granted["roles"]
        revoked = client.post(
            "/users/USR-02/privileges", json={"role": "admin", "action": "revoke"}
        ).json()
        assert "admin" not in revoked["roles"]

    def test_revoke_missing_role_conflicts(self, client: TestClient) -> None:
        response = client.post(
            "/users/USR-02/privileges", json={"role": "admin", "action": "revoke"}
        )
        assert response.status_code == 409

    def test_delete_records_empties_set(self, client: TestClient) -> None:
        result = client.delete("/records/tmp_sessions").json()
        assert result["deleted_count"] == 48213
        remaining = client.get("/records").json()
        tmp = next(r for r in remaining if r["name"] == "tmp_sessions")
        assert tmp["record_count"] == 0


class TestAdmin:
    def test_health(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["seed"] == 42

    def test_reset_restores_fixtures(self, client: TestClient) -> None:
        client.delete("/records/tmp_sessions")
        client.post("/incidents/INC-1001/resolve", json={"resolution_note": "x"})
        assert client.post("/admin/reset").status_code == 200
        records = client.get("/records").json()
        tmp = next(r for r in records if r["name"] == "tmp_sessions")
        assert tmp["record_count"] == 48213
        assert client.get("/incidents/INC-1001").json()["state"] == "new"
