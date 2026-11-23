"""Sanity checks on scoring.yaml so a hand edit can't break the model's invariants."""
from pathlib import Path

import yaml

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "scoring.yaml").read_text())


def test_fit_maxes_out_at_60():
    fit = CONFIG["fit"]
    best = (
        max(band["points"] for band in fit["employees"])
        + fit["industry"]["core"]["points"]
        + fit["tech_stack"]["cap"]
        + max(fit["persona"].values())
    )
    assert best == 60


def test_intent_cap_is_40():
    assert CONFIG["intent"]["cap"] == 40


def test_employee_bands_are_contiguous():
    bands = CONFIG["fit"]["employees"]
    for lower, upper in zip(bands, bands[1:]):
        assert upper["min"] == lower["max"] + 1


def test_thresholds_sit_inside_controller_limits():
    t, limits = CONFIG["thresholds"], CONFIG["controller"]["limits"]
    for tier in ("hot", "warm"):
        assert limits[tier]["floor"] <= t[tier] <= limits[tier]["ceiling"]
    assert t["hot"] - t["warm"] >= limits["min_gap_hot_warm"]


def test_setpoint_bands_are_ordered():
    for tier in ("hot", "warm"):
        band = CONFIG["setpoints"][tier]
        assert 0 < band["low"] < band["high"] < 1


def test_controller_mode_is_known():
    assert CONFIG["controller"]["mode"] in {"approval", "autonomous"}
