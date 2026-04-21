from beaverpush import _version


def test_read_bundled_version_strips_utf8_bom(monkeypatch, tmp_path):
    (tmp_path / "version.txt").write_text("\ufeff1.2.3\n", encoding="utf-8")
    monkeypatch.setattr(_version, "_get_assets_dir", lambda: tmp_path)
    assert _version._read_bundled_version() == "1.2.3"
