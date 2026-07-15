from __future__ import annotations

import pytest

from backend.scripts.current_progol_confidence_report import select_slate


def test_select_slate_defaults_to_first_api_slate() -> None:
    slates = [{"id": "s1", "draw_code": "PGM-804"}, {"id": "s2", "draw_code": "PG-2342"}]

    assert select_slate(slates)["id"] == "s1"


def test_select_slate_by_id_or_draw_code() -> None:
    slates = [{"id": "s1", "draw_code": "PGM-804"}, {"id": "s2", "draw_code": "PG-2342"}]

    assert select_slate(slates, slate_id="s2")["draw_code"] == "PG-2342"
    assert select_slate(slates, draw_code="pgm-804")["id"] == "s1"


def test_select_slate_exits_when_requested_slate_is_missing() -> None:
    with pytest.raises(SystemExit):
        select_slate([{"id": "s1", "draw_code": "PGM-804"}], draw_code="PG-9999")
