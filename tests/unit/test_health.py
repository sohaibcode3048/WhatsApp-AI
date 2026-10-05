"""Unit tests for health and liveness endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_root(async_client: AsyncClient):
    """Verify /health returns 200 OK with expected schema and service name."""
    response = await async_client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "whatsapp-ai-munshi"
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data
    assert data["components"]["api"]["status"] == "up"

    # Middleware correlation header checks
    assert "x-request-id" in response.headers
    assert "x-response-time-ms" in response.headers


@pytest.mark.asyncio
async def test_health_endpoint_versioned(async_client: AsyncClient):
    """Verify /api/v1/health returns 200 OK with expected schema."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_ping_endpoint(async_client: AsyncClient):
    """Verify /ping returns pong for simple container probes."""
    response = await async_client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"ping": "pong"}


@pytest.mark.asyncio
async def test_request_id_correlation(async_client: AsyncClient):
    """Verify client-supplied X-Request-ID is preserved in response headers."""
    custom_id = "test-req-corr-12345"
    response = await async_client.get("/health", headers={"X-Request-ID": custom_id})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == custom_id
