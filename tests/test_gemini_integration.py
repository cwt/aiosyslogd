#!/usr/bin/env python
"""
Test script to verify the Gemini integration implementation
"""

import sys
import os
import pytest

# Add the project root to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aiosyslogd.web import app


@pytest.mark.asyncio
async def test_routes():
    """Test that the new routes exist"""
    async with app.test_client() as client:
        # Test that the main page loads
        response = await client.get("/")
        assert response.status_code != 404, "Main page should be accessible"

        # Test that the API endpoints exist (will return 302 redirect since not logged in, but shouldn't return 404)
        response = await client.post(
            "/api/gemini-search", json={"query": "test"}
        )
        assert response.status_code in [
            401,
            405,
            302,
        ], f"Gemini search endpoint should exist (got {response.status_code})"

        response = await client.post(
            "/api/save-gemini-key", json={"api_key": "test"}
        )
        assert response.status_code in [
            401,
            405,
            302,
        ], f"Save key endpoint should exist (got {response.status_code})"

        # Test API endpoints
        response = await client.get("/api/check-gemini-auth")
        assert response.status_code in [
            401,
            405,
            302,
        ], f"Auth check endpoint should exist (got {response.status_code})"

        print("✓ All routes exist and are accessible")
