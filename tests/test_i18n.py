"""core/i18n.py language choice and lookup; tools/i18n_check.py on the real repo."""

from __future__ import annotations

import json

import pytest

from core import i18n


@pytest.fixture
def locales(tmp_path, monkeypatch):
    (tmp_path / "th.json").write_text(json.dumps({"Hello": "สวัสดี", "Got {n} frames": "ได้ {n} เฟรม"},
                                                 ensure_ascii=False), encoding="utf-8")
    (tmp_path / "_th_part_1.json").write_text("{}", encoding="utf-8")       # merge parts aren't languages
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n._table.cache_clear()
    yield tmp_path
    i18n._table.cache_clear()


def test_supported(locales):
    assert i18n.supported() == {"en", "th"}


@pytest.mark.parametrize("cookie,accept,expected", [
    ("th", None, "th"),
    ("th", "en-US,en;q=0.9", "th"),           # the cookie wins
    ("en", "th-TH", "en"),
    ("xx", "th-TH,th;q=0.9", "th"),           # unsupported cookie -> Accept-Language
    (None, "fr-FR,fr;q=0.9,th;q=0.8", "th"),  # first supported language
    (None, "TH", "th"),
    (None, "fr-FR", "en"),
    (None, None, "en"),
    ("", "", "en"),
])
def test_pick(locales, cookie, accept, expected):
    assert i18n.pick(cookie, accept) == expected


def test_translate_english_default(locales):
    assert i18n.current() == "en"
    assert i18n._("Hello") == "Hello"
    assert i18n._("Got {n} frames", n=7) == "Got 7 frames"


def test_translate_thai(locales):
    token = i18n.set_lang("th")
    try:
        assert i18n.current() == "th"
        assert i18n._("Hello") == "สวัสดี"
        assert i18n._("Got {n} frames", n=7) == "ได้ 7 เฟรม"
        assert i18n._("Not in the table") == "Not in the table"
        assert i18n._("Missing {x}", x=1) == "Missing 1"
    finally:
        i18n.reset(token)
    assert i18n.current() == "en"


def test_unsupported_language_falls_back(locales):
    token = i18n.set_lang("de")
    try:
        assert i18n.current() == "en"
    finally:
        i18n.reset(token)


def test_no_table_returns_english(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "LOCALES_DIR", tmp_path)
    i18n._table.cache_clear()
    try:
        assert i18n.supported() == {"en"}
        token = i18n.set_lang("th")
        try:
            assert i18n.current() == "en"
            assert i18n._("Hello") == "Hello"
        finally:
            i18n.reset(token)
    finally:
        i18n._table.cache_clear()


def test_real_thai_table_is_valid_json():
    table = json.loads((i18n.LOCALES_DIR / "th.json").read_text(encoding="utf-8"))
    assert isinstance(table, dict) and len(table) > 100
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in table.items())


def test_i18n_check_has_no_missing_strings(capsys):
    from tools import i18n_check

    assert i18n_check.main() == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "0 missing" in out
