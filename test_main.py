from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock
from main import app

client = TestClient(app)

# Mock payload that mimics the exact structure returned by elprisetjustnu.se
MOCK_PRICES_RESPONSE = [
    {"SEK_per_kWh": 1.5, "time_start": "2026-04-22T00:00:00+02:00"},
    {"SEK_per_kWh": 0.5, "time_start": "2026-04-22T01:00:00+02:00"}, # Cheapest hour
    {"SEK_per_kWh": 2.0, "time_start": "2026-04-22T02:00:00+02:00"}
]

@patch("main.httpx.AsyncClient.get", new_callable=AsyncMock)
def test_calculate_cheapest_time(mock_get):
    # Setup the mock to intercept the external API call
    # Use MagicMock here because response.json() is a synchronous method in httpx
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = MOCK_PRICES_RESPONSE
    
    mock_get.return_value = mock_response

    # Define the input payload for your API
    request_payload = {
        "consumption_kwh": 2.5,
        "duration_mins": 90
    }

    # Execute the POST request against the local test client
    response = client.post("/calculate-cost", json=request_payload)

    # Validate the results
    assert response.status_code == 200
    
    data = response.json()
    
    # Verify the algorithm correctly identified the cheapest hour from the mock data
    assert data["cheapest_start_time"] == "2026-04-22T01:00:00+02:00"
    
    # Verify the math: 0.5 SEK * 2.5 kWh = 1.25 SEK
    assert data["estimated_cost_sek"] == 1.25
    assert data["zone"] == "SE3"