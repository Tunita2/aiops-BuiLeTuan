import pytest
from fastapi.testclient import TestClient
from serve import app

client = TestClient(app)

def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_readyz():
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "graph" in data["checks"]
    assert "history" in data["checks"]

def test_version():
    response = client.get("/version")
    assert response.status_code == 200
    data = response.json()
    assert "app" in data
    assert "pipeline_config" in data
    assert "graph_version" in data

def test_incident_empty():
    response = client.post("/incident", json={"alerts": []})
    assert response.status_code == 400
    assert "detail" in response.json()

def test_incident_invalid_schema():
    # Missing required fields like 'id' and 'service'
    payload = {
        "alerts": [
            {
                "ts": "2026-06-12T09:42:01Z",
                "metric": "db_connection_pool_used_ratio",
                "severity": "warn",
                "value": 0.85,
                "threshold": 0.80
            }
        ]
    }
    response = client.post("/incident", json=payload)
    assert response.status_code == 422

def test_incident_valid():
    payload = {
        "alerts": [
            {
                "id": "a-0001",
                "ts": "2026-06-12T09:42:01Z",
                "service": "payment-svc",
                "metric": "db_connection_pool_used_ratio",
                "severity": "warn",
                "value": 0.85,
                "threshold": 0.80,
                "labels": {"env": "prod"}
            },
            {
                "id": "a-0003",
                "ts": "2026-06-12T09:42:22Z",
                "service": "payment-svc",
                "metric": "latency_p99_ms",
                "severity": "crit",
                "value": 1840,
                "threshold": 800,
                "labels": {"env": "prod"}
            },
            {
                "id": "a-0005",
                "ts": "2026-06-12T09:42:55Z",
                "service": "checkout-svc",
                "metric": "http_5xx_rate",
                "severity": "crit",
                "value": 12.4,
                "threshold": 1.0,
                "labels": {"env": "prod"}
            }
        ]
    }
    response = client.post("/incident", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "clusters" in data
    assert "root_cause" in data
    assert "recommended_actions" in data
    assert "similar_incidents" in data
    
    # Check cluster format
    assert len(data["clusters"]) > 0
    assert data["clusters"][0]["cluster_id"] == "c-000-000"
    
    # Check root cause format
    assert data["root_cause"]["service"] == "payment-svc"
    assert data["root_cause"]["confidence"] > 0
    assert len(data["recommended_actions"]) > 0
