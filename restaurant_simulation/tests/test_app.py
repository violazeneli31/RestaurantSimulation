import app as app_module


def test_station_with_zero_chefs_is_raised_to_one():
    cfg = app_module._clean_config({"grill_chefs": 0, "pasta_chefs": 0, "dessert_chefs": 0})
    assert (cfg["grill_chefs"], cfg["pasta_chefs"], cfg["dessert_chefs"]) == (1, 1, 1)


def test_bad_values_fall_back_to_defaults():
    cfg = app_module._clean_config({"num_stoves": "abc", "max_orders": -5})
    assert cfg["num_stoves"] == app_module.DEFAULT_CONFIG["num_stoves"]
    assert cfg["max_orders"] == 1


def test_pages_and_api_respond():
    client = app_module.build_app().test_client()
    assert client.get("/").status_code == 200
    assert client.get("/benchmark").status_code == 200
    state = client.get("/api/state").get_json()
    assert {"active", "served", "chefs", "stats", "events"} <= state.keys()
    r = client.post("/api/restart", json={"grill_chefs": 0, "max_orders": 4})
    assert r.get_json()["config"]["grill_chefs"] == 1
