import pytest
import httpx
from fastapi import FastAPI

# Import the app instance from your API module
# Adjust the import path according to your project structure
from scheduling_agent.api.main import app

@pytest.mark.asyncio
async def test_read_docs():
    """Test if the /docs endpoint is accessible and returns 200 OK."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/docs")
    assert response.status_code == 200
    # More robust check for Swagger UI content
    assert "SwaggerUIBundle" in response.text # Check for key JS object

@pytest.mark.asyncio
async def test_read_root():
    """Test the root endpoint."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "A2A Scheduling Agent is running."} 