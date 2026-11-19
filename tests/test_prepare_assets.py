"""Transferred GGUF validation must fail closed before downloads begin."""
import hashlib
import json
import pytest
from scripts import prepare_assets


@pytest.fixture
def model_copy(tmp_path, monkeypatch):
    payload = b"GGUFtest"
    (tmp_path / "models").mkdir()
    model = tmp_path / "models" / "test.gguf"
    model.write_bytes(payload)
    manifest = {"filename": model.name, "size_bytes": len(payload),
                "sha256_local_reference": hashlib.sha256(payload).hexdigest()}
    (tmp_path / "model-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(prepare_assets, "ROOT", tmp_path)
    return model


def test_valid_transferred_model(model_copy):
    prepare_assets.check_model(verify=True)


def test_missing_model_stops_preparation(model_copy):
    model_copy.unlink()
    with pytest.raises(RuntimeError, match="Place the separately"):
        prepare_assets.check_model()


def test_wrong_header_stops_preparation(model_copy):
    model_copy.write_bytes(b"BAD!test")
    with pytest.raises(RuntimeError, match="GGUF header"):
        prepare_assets.check_model()


def test_truncated_model_stops_preparation(model_copy):
    model_copy.write_bytes(b"GGUF")
    with pytest.raises(RuntimeError, match="Model size differs"):
        prepare_assets.check_model()


def test_same_size_changed_model_fails_checksum(model_copy):
    model_copy.write_bytes(b"GGUFedit")
    with pytest.raises(RuntimeError, match="checksum differs"):
        prepare_assets.check_model(verify=True)
