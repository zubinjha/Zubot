import json
from pathlib import Path

import pytest

from src.zubot.core.config_loader import clear_config_cache
from src.zubot.tools.kernel.location import get_location


def test_get_location_contract_shape():
    result = get_location()
    expected_keys = {"lat", "lon", "city", "region", "country", "timezone", "source"}
    assert set(result.keys()) == expected_keys
    assert result["source"] in {"config_home_location", "unresolved"}


def test_get_location_uses_home_location_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "timezone": "America/New_York",
                "home_location": {
                    "lat": 40.0931,
                    "lon": -83.0170,
                    "city": "Worthington",
                    "region": "Ohio",
                    "country": "USA",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ZUBOT_CONFIG_PATH", str(config_path))
    clear_config_cache()

    result = get_location()
    assert result["source"] == "config_home_location"
    assert result["lat"] == 40.0931
    assert result["lon"] == -83.0170
    assert result["city"] == "Worthington"
    assert result["region"] == "Ohio"
    assert result["country"] == "USA"
    assert result["timezone"] == "America/New_York"
