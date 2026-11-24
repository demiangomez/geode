"""Tests for geode.rinex_qc.config: load/override and config_hash."""
import json

import pytest

from geode.rinex_qc.config import RinexQCConfig, RinexQCConfigError


def test_defaults():
    config = RinexQCConfig.load()
    assert config.elev_cutoff_deg == 10.0
    assert config.orbit_source == 'brdc'
    assert config.systems == ()
    assert config.ringo_bin == 'ringo'


def test_cli_overrides_win_over_json(tmp_path):
    json_path = tmp_path / 'config.json'
    json_path.write_text(json.dumps({'elev_cutoff_deg': 15.0, 'orbit_source': 'sp3'}))

    config = RinexQCConfig.load(json_path=str(json_path), elev_cutoff_deg=20.0)
    assert config.elev_cutoff_deg == 20.0   # CLI switch wins
    assert config.orbit_source == 'sp3'     # untouched JSON value survives


def test_cli_none_does_not_override_json(tmp_path):
    json_path = tmp_path / 'config.json'
    json_path.write_text(json.dumps({'elev_cutoff_deg': 15.0}))

    config = RinexQCConfig.load(json_path=str(json_path), elev_cutoff_deg=None)
    assert config.elev_cutoff_deg == 15.0


def test_unknown_json_key_rejected(tmp_path):
    json_path = tmp_path / 'config.json'
    json_path.write_text(json.dumps({'not_a_real_key': 1}))

    with pytest.raises(RinexQCConfigError):
        RinexQCConfig.load(json_path=str(json_path))


def test_invalid_orbit_source_rejected():
    with pytest.raises(RinexQCConfigError):
        RinexQCConfig.load(orbit_source='not_a_source')


def test_config_hash_same_for_science_equivalent_configs():
    a = RinexQCConfig.load(elev_cutoff_deg=10.0, work_dir='/tmp/a')
    b = RinexQCConfig.load(elev_cutoff_deg=10.0, work_dir='/tmp/b', keep_tool_outputs=True,
                           ringo_bin='/opt/ringo/bin/ringo')
    assert a.config_hash == b.config_hash  # work_dir/keep_tool_outputs/ringo_bin don't affect the hash


def test_config_hash_differs_for_different_science_settings():
    a = RinexQCConfig.load(elev_cutoff_deg=10.0)
    b = RinexQCConfig.load(elev_cutoff_deg=15.0)
    assert a.config_hash != b.config_hash


def test_config_hash_stable_across_runs():
    assert RinexQCConfig.load().config_hash == RinexQCConfig.load().config_hash
