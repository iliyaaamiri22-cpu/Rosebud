"""Backend API tests for Rosebud Checkout Bot"""
import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')


class TestRootEndpoint:
    def test_root_returns_message(self):
        r = requests.get(f"{BASE_URL}/api/")
        assert r.status_code == 200
        data = r.json()
        assert "message" in data
        assert "Rosebud" in data["message"]
        assert "bot_active" in data

    def test_bot_active_flag(self):
        r = requests.get(f"{BASE_URL}/api/")
        data = r.json()
        assert isinstance(data["bot_active"], bool)


class TestStatusEndpoint:
    def test_status_200(self):
        r = requests.get(f"{BASE_URL}/api/status")
        assert r.status_code == 200

    def test_status_has_bot_active(self):
        r = requests.get(f"{BASE_URL}/api/status")
        data = r.json()
        assert "bot_active" in data
        assert data["bot_active"] is True

    def test_status_has_checkout_counts(self):
        r = requests.get(f"{BASE_URL}/api/status")
        data = r.json()
        assert "total_checkouts" in data
        assert "successful_checkouts" in data
        assert isinstance(data["total_checkouts"], int)
        assert isinstance(data["successful_checkouts"], int)


class TestCheckoutsEndpoint:
    def test_checkouts_200(self):
        r = requests.get(f"{BASE_URL}/api/checkouts")
        assert r.status_code == 200

    def test_checkouts_returns_list(self):
        r = requests.get(f"{BASE_URL}/api/checkouts")
        data = r.json()
        assert isinstance(data, list)

    def test_checkouts_no_mongo_id(self):
        """Ensure _id is not leaked in response"""
        r = requests.get(f"{BASE_URL}/api/checkouts")
        data = r.json()
        for item in data:
            assert "_id" not in item
