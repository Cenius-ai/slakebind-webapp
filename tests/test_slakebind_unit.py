"""Pure-function tests: the maths, the parsers and the storage helpers."""

import ast
import json
import sys
from decimal import Decimal

import pytest
from conftest import SCRIPT, combined  # noqa: F401  (combined re-exported for siblings)


# --- ratio maths ----------------------------------------------------------


def test_ratio_of_the_worked_example(sb):
    result = sb.compute_ratio(Decimal("8.5"), "kg", Decimal("340"), "g")
    assert result.hide_grams == Decimal("8500")
    assert result.tannin_grams == Decimal("340")
    assert result.percent == Decimal("4")
    assert result.ratio == Decimal("25")


def test_ratio_normalises_pounds_and_ounces(sb):
    result = sb.compute_ratio(Decimal("18.7"), "lb", Decimal("12"), "oz")
    assert result.hide_grams == Decimal("18.7") * Decimal("453.59237")
    assert result.tannin_grams == Decimal("12") * Decimal("28.349523125")
    # 12 oz against 18.7 lb is just over 4%
    assert Decimal("4") < result.percent < Decimal("4.1")


def test_no_float_rounding_leaks_into_the_ratio(sb):
    result = sb.compute_ratio(Decimal("10"), "kg", Decimal("400"), "g")
    assert result.percent == Decimal("4")
    assert result.ratio == Decimal("25")
    assert sb.trim(result.percent, 2, min_places=1) == "4.0"


# --- display helpers ------------------------------------------------------


@pytest.mark.parametrize(
    "value, places, min_places, expected",
    [
        (Decimal("4"), 2, 1, "4.0"),
        (Decimal("4.01"), 2, 1, "4.01"),
        (Decimal("25"), 2, 0, "25"),
        (Decimal("24.93"), 2, 0, "24.93"),
        (Decimal("3.333333"), 2, 0, "3.33"),
        (Decimal("25000"), 6, 0, "25000"),
    ],
)
def test_trim_renders_decimals_readably(sb, value, places, min_places, expected):
    assert sb.trim(value, places, min_places) == expected


@pytest.mark.parametrize(
    "seconds, expected",
    [(5400, "1h 30m"), (45, "45s"), (135, "2m 15s"), (3600, "1h 00m"), (5410, "1h 30m 10s")],
)
def test_fmt_duration_uses_plain_words(sb, seconds, expected):
    assert sb.fmt_duration(seconds) == expected


@pytest.mark.parametrize("seconds, expected", [(90, "01:30"), (3661, "1:01:01"), (0, "00:00")])
def test_fmt_clock(sb, seconds, expected):
    assert sb.fmt_clock(seconds) == expected


# --- amount parsing -------------------------------------------------------


def test_parse_amount_reads_inline_units(sb):
    assert sb.parse_amount("8.5kg", "hide weight", "kg", ("kg", "lb")) == (Decimal("8.5"), "kg")
    assert sb.parse_amount("340 g", "tannin amount", "g", ("g", "oz")) == (Decimal("340"), "g")
    assert sb.parse_amount("12OZ", "tannin amount", "g", ("g", "oz")) == (Decimal("12"), "oz")


def test_parse_amount_falls_back_to_the_unit_system(sb):
    assert sb.parse_amount("18.7", "hide weight", "lb", ("kg", "lb"))[1] == "lb"
    assert sb.parse_amount("8", "quantity", "kg", ("g", "kg", "oz", "lb"))[1] == "kg"


@pytest.mark.parametrize("raw", ["0kg", "-3kg", "abc", "", "8.5stone", "nan kg", "1e12 kg"])
def test_parse_amount_rejects_bad_input(sb, raw):
    with pytest.raises(sb.InputError):
        sb.parse_amount(raw, "hide weight", "kg", ("kg", "lb"))


# --- duration parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("90", 5400.0),
        ("90m", 5400.0),
        ("1h30m", 5400.0),
        ("45s", 45.0),
        ("2h", 7200.0),
        ("1.5h", 5400.0),
        ("1h30m10s", 5410.0),
        ("5 minutes", 300.0),
    ],
)
def test_parse_duration(sb, raw, expected):
    assert sb.parse_duration(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["soon", "", "-5", "5x", "h", "1h30q"])
def test_parse_duration_rejects_bad_input(sb, raw):
    with pytest.raises(sb.InputError):
        sb.parse_duration(raw)


# --- ledger storage -------------------------------------------------------


def test_make_entry_records_both_units(sb):
    entry = sb.make_entry("oak", "Hill Farm", Decimal("25"), "kg", "2025-04-01", "spring bark", [])
    assert len(entry["id"]) == 8
    assert entry["quantity"] == "25"
    assert entry["unit"] == "kg"
    assert entry["quantity_g"] == "25000"
    assert entry["bath_date"] == "2025-04-01"


def test_make_entry_mints_a_fresh_id_every_time(sb):
    first = sb.make_entry("oak", "", Decimal("1"), "kg", "2025-01-01", "", [])
    second = sb.make_entry("oak", "", Decimal("1"), "kg", "2025-01-01", "", [first])
    assert first["id"] != second["id"]


def test_save_then_load_round_trips(sb, tmp_path):
    path = str(tmp_path / "nested" / "ledger.json")
    document = sb.empty_ledger()
    document["entries"].append(
        sb.make_entry("oak", "Hill Farm", Decimal("25"), "kg", "2025-04-01", "", [])
    )
    sb.save_ledger(document, path)
    assert sb.load_ledger(path) == document


def test_load_ledger_creates_nothing_on_a_fresh_machine(sb, tmp_path):
    path = str(tmp_path / "missing" / "ledger.json")
    assert sb.load_ledger(path) == {"version": 1, "entries": []}
    assert not (tmp_path / "missing").exists()


def test_leftover_temp_file_does_not_disturb_the_ledger(sb, tmp_path, home, monkeypatch):
    monkeypatch.setenv("SLAKEBIND_HOME", str(home))
    document = sb.empty_ledger()
    document["entries"].append(
        sb.make_entry("birch", "Ridge Stand", Decimal("12.5"), "kg", "2025-03-14", "", [])
    )
    sb.save_ledger(document, sb.ledger_path())
    (home / ".ledger-halfwritten.tmp").write_text("{ truncated", encoding="utf-8")
    reloaded = sb.load_ledger(sb.ledger_path())
    assert len(reloaded["entries"]) == 1
    assert reloaded["entries"][0]["species"] == "birch"


def test_filter_entries_is_inclusive_on_both_ends(sb):
    entries = [
        {"id": "a", "species": "oak", "bath_date": "2025-01-01", "quantity_g": "1", "quantity": "1", "unit": "g"},
        {"id": "b", "species": "oak", "bath_date": "2025-06-30", "quantity_g": "1", "quantity": "1", "unit": "g"},
        {"id": "c", "species": "birch", "bath_date": "2025-03-01", "quantity_g": "1", "quantity": "1", "unit": "g"},
    ]
    chosen = sb.filter_entries(entries, "OAK", "2025-01-01", "2025-06-30")
    assert [e["id"] for e in chosen] == ["a", "b"]


def test_newest_first_orders_by_bath_date(sb):
    entries = [
        {"id": "old", "bath_date": "2025-01-01", "created_at": "2025-01-01T00:00:00"},
        {"id": "new", "bath_date": "2025-06-01", "created_at": "2025-06-01T00:00:00"},
    ]
    assert [e["id"] for e in sb.newest_first(entries)] == ["new", "old"]


# --- packaging promises ---------------------------------------------------


def test_only_standard_library_modules_are_imported():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"__future__"}
    forbidden = {"socket", "urllib", "http", "requests", "ftplib", "smtplib", "xmlrpc", "ssl"}
    assert not (names & forbidden)


def test_no_networking_module_text_anywhere_in_the_source():
    source = SCRIPT.read_text(encoding="utf-8")
    for probe in ("import socket", "import urllib", "import http", "import requests", "socket.socket"):
        assert probe not in source


def test_shipped_files_the_docs_promise_are_all_present():
    from conftest import ROOT

    for relative in (
        "slakebind.py",
        "pyproject.toml",
        "requirements-dev.txt",
        "install.sh",
        "demo.sh",
        "README.md",
        "INSTALL.md",
        "USAGE.md",
        ".env.example",
        "examples/sample-ledger.json",
    ):
        assert (ROOT / relative).exists(), "missing " + relative


def test_sample_ledger_is_a_valid_slakebind_ledger(sb):
    from conftest import ROOT

    document = json.loads((ROOT / "examples" / "sample-ledger.json").read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert len(document["entries"]) >= 5
    assert len({e["species"] for e in document["entries"]}) >= 3
    for entry in document["entries"]:
        assert {"id", "bath_date", "species", "quantity", "unit", "quantity_g"} <= set(entry)
        assert Decimal(entry["quantity_g"]) == Decimal(entry["quantity"]) * sb.UNIT_GRAMS[entry["unit"]]
