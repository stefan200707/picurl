from unittest.mock import patch, MagicMock, AsyncMock
import pytest
from app.geo.poi import fetch_poi, POICategory, POIResult

@pytest.mark.asyncio
async def test_fetch_poi_other():
    result = await fetch_poi(55.75, 37.61, POICategory.OTHER, 500)
    assert result.count == 0
    assert result.closest_distance_m is None

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_fetch_poi_with_results(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {"lat": 55.751, "lon": 37.611},
            {"lat": 55.752, "lon": 37.612}
        ]
    }
    mock_post.return_value = mock_response

    result = await fetch_poi(55.75, 37.61, POICategory.SCHOOL, 500)
    assert result.count == 2
    assert result.closest_distance_m is not None
    assert result.closest_distance_m > 0

@pytest.mark.asyncio
@patch("httpx.AsyncClient.post")
async def test_fetch_poi_empty(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {"elements": []}
    mock_post.return_value = mock_response

    result = await fetch_poi(55.75, 37.61, POICategory.SHOP, 500)
    assert result.count == 0
    assert result.closest_distance_m is None
