"""End-to-end tests: the real CLI, run as a subprocess, plus two pty runs."""

import json
import os
import pty
import select
import signal
import subprocess
import sys
import time

import pytest
from conftest import ROOT, SCRIPT, combined

ANSI = "\x1b"


# --------------------------------------------------------------------------
# top-level command surface
# --------------------------------------------------------------------------


def test_no_arguments_prints_the_help_first_screen(run):
    result = run()
    assert result.returncode == 0
    assert "slakebind 1.0.0" in result.stdout
    for command in ("ratio", "soak", "ledger add", "ledger list", "ledger remove"):
        assert command in result.stdout
    assert "slakebind ratio --hide 8.5kg --tannin 340g" in result.stdout


def test_version_flag(run):
    result = run("--version")
    assert result.returncode == 0
    assert result.stdout.strip() == "slakebind 1.0.0"


def test_unknown_command_prints_usage_and_fails(run):
    result = run("wibble")
    assert result.returncode != 0
    assert "usage:" in combined(result)
    assert "Traceback" not in combined(result)


def test_unknown_flag_prints_usage_and_fails(run):
    result = run("ratio", "--nope")
    assert result.returncode != 0
    assert "usage:" in combined(result)
    assert "Traceback" not in combined(result)


def test_every_subcommand_has_help_with_an_example(run):
    for args in (("ratio",), ("soak",), ("ledger", "add"), ("ledger", "list"), ("ledger", "remove")):
        result = run(*args, "--help")
        assert result.returncode == 0, args
        assert "examples:" in result.stdout, args
        assert "slakebind " in result.stdout, args


# --------------------------------------------------------------------------
# ratio
# --------------------------------------------------------------------------


def test_ratio_metric_matches_the_worked_example(run):
    result = run("ratio", "--hide", "8.5kg", "--tannin", "340g")
    assert result.returncode == 0
    assert "8.5 kg" in result.stdout and "18.7393 lb" in result.stdout
    assert "340 g" in result.stdout and "11.9931 oz" in result.stdout
    assert "4.0%" in result.stdout
    assert "1:25" in result.stdout
    assert "340 g tannin / 8500 g hide" in result.stdout


def test_ratio_imperial_reads_a_unitless_weight_as_pounds(run):
    result = run("ratio", "--hide", "18.7", "--tannin", "12oz", "--unit", "imperial")
    assert result.returncode == 0
    assert "18.7 lb" in result.stdout
    assert "12 oz" in result.stdout
    assert "4.01%" in result.stdout


def test_ratio_metric_reads_a_unitless_weight_as_kilograms(run):
    result = run("ratio", "--hide", "10", "--tannin", "400")
    assert result.returncode == 0
    assert "10 kg" in result.stdout
    assert "400 g" in result.stdout
    assert "1:25" in result.stdout


@pytest.mark.parametrize(
    "hide, tannin, named_field",
    [
        ("0kg", "340g", "hide weight"),
        ("-4kg", "340g", "hide weight"),
        ("abc", "340g", "hide weight"),
        ("8.5kg", "0g", "tannin amount"),
        ("8.5kg", "nope", "tannin amount"),
    ],
)
def test_ratio_rejects_bad_values_in_one_plain_sentence(run, hide, tannin, named_field):
    result = run("ratio", "--hide", hide, "--tannin", tannin)
    assert result.returncode != 0
    message = combined(result)
    assert named_field in message
    assert "Traceback" not in message
    assert len([line for line in message.strip().splitlines() if line.strip()]) == 1


def test_ratio_without_arguments_invites_a_first_command(run):
    result = run("ratio")
    assert result.returncode != 0
    assert "slakebind ratio --hide 8.5kg --tannin 340g" in combined(result)


# --------------------------------------------------------------------------
# soak
# --------------------------------------------------------------------------


def test_soak_no_wait_prints_the_target_time_and_returns(run):
    started = time.time()
    result = run("soak", "1h30m", "--no-wait")
    assert time.time() - started < 10
    assert result.returncode == 0
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "pull at" in lines[0]
    assert "1h 30m" in lines[0]


def test_soak_in_a_pipe_degrades_to_one_plain_line(run):
    result = run("soak", "5m")
    assert result.returncode == 0
    assert "\r" not in result.stdout
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "pull at" in lines[0]


@pytest.mark.parametrize("duration", ["soon", "5x", "1h30q", ""])
def test_soak_rejects_an_unparseable_duration(run, duration):
    result = run("soak", duration)
    assert result.returncode != 0
    message = combined(result)
    assert "Traceback" not in message
    if duration:
        assert "1h30m" in message


def test_soak_without_a_duration_shows_the_accepted_formats(run):
    result = run("soak")
    assert result.returncode != 0
    message = combined(result)
    assert "90m" in message and "1h30m" in message


# --------------------------------------------------------------------------
# ledger add
# --------------------------------------------------------------------------


def read_ledger(home):
    return json.loads((home / "ledger.json").read_text(encoding="utf-8"))


def test_first_add_creates_the_folder_and_the_file(run, home):
    assert not home.exists()
    result = run(
        "ledger", "add", "--species", "oak", "--source", "Hill Farm",
        "--qty", "25kg", "--date", "2025-04-01", "--note", "spring bark",
    )
    assert result.returncode == 0
    assert "created" in result.stdout
    document = read_ledger(home)
    assert document["version"] == 1
    assert len(document["entries"]) == 1
    entry = document["entries"][0]
    assert entry["species"] == "oak"
    assert entry["source"] == "Hill Farm"
    assert entry["quantity"] == "25"
    assert entry["unit"] == "kg"
    assert entry["quantity_g"] == "25000"
    assert entry["bath_date"] == "2025-04-01"
    assert entry["note"] == "spring bark"
    assert len(entry["id"]) == 8


def test_add_confirmation_names_the_id_species_quantity_and_date(run):
    result = run("ledger", "add", "--species", "oak", "--qty", "25kg", "--date", "2025-04-01")
    assert result.returncode == 0
    added = next(line for line in result.stdout.splitlines() if line.startswith("added "))
    assert "oak" in added and "25 kg" in added and "2025-04-01" in added
    assert added.split()[1] in result.stdout


def test_add_without_a_date_defaults_to_today(run, home):
    result = run("ledger", "add", "--species", "oak", "--qty", "25kg")
    assert result.returncode == 0
    today = time.strftime("%Y-%m-%d")
    assert read_ledger(home)["entries"][0]["bath_date"] == today
    assert today in result.stdout


def test_add_twice_writes_two_distinct_entries(run, home):
    run("ledger", "add", "--species", "oak", "--qty", "25kg")
    run("ledger", "add", "--species", "oak", "--qty", "25kg")
    entries = read_ledger(home)["entries"]
    assert len(entries) == 2
    assert entries[0]["id"] != entries[1]["id"]


@pytest.mark.parametrize(
    "args",
    [
        ("--qty", "0kg"),
        ("--species", "oak", "--qty", "-5kg"),
        ("--species", "oak", "--qty", "nope"),
        ("--species", "   ", "--qty", "25kg"),
        ("--species", "oak", "--qty", "25kg", "--date", "not-a-date"),
    ],
)
def test_add_rejects_bad_input_and_writes_nothing(run, home, args):
    result = run("ledger", "add", *args)
    assert result.returncode != 0
    assert "Traceback" not in combined(result)
    assert not (home / "ledger.json").exists()


def test_add_accepts_an_inline_imperial_quantity(run, home):
    result = run("ledger", "add", "--species", "oak", "--qty", "8lb")
    assert result.returncode == 0
    assert read_ledger(home)["entries"][0]["quantity_g"] == "3628.73896"


# --------------------------------------------------------------------------
# ledger list
# --------------------------------------------------------------------------


def seed_three(run):
    run("ledger", "add", "--species", "oak", "--source", "Hill Farm", "--qty", "25kg", "--date", "2025-04-01")
    run("ledger", "add", "--species", "birch", "--qty", "12.5kg", "--date", "2025-03-14")
    run("ledger", "add", "--species", "oak", "--source", "Mill Creek", "--qty", "8lb", "--date", "2025-05-02")


def test_list_prints_newest_first_with_a_count_line(run):
    seed_three(run)
    result = run("ledger", "list")
    assert result.returncode == 0
    body = result.stdout.splitlines()
    rows = [line for line in body if line.strip()]
    assert "BATH DATE" in rows[0] and "SPECIES" in rows[0] and "QTY" in rows[0]
    assert "2025-05-02" in rows[2]
    assert "2025-04-01" in rows[3]
    assert "2025-03-14" in rows[4]
    assert rows[-1] == "3 entries listed"


def test_list_filters_by_species_and_inclusive_date_window(run):
    seed_three(run)
    result = run("ledger", "list", "--species", "oak", "--from", "2025-01-01", "--to", "2025-04-30")
    assert result.returncode == 0
    assert "2025-04-01" in result.stdout
    assert "2025-05-02" not in result.stdout
    assert "2025-03-14" not in result.stdout
    assert "1 entry listed" in result.stdout


def test_list_with_no_match_exits_zero_and_shows_the_filters(run):
    seed_three(run)
    result = run("ledger", "list", "--species", "elm")
    assert result.returncode == 0
    assert "no entries match" in result.stdout
    assert 'species contains "elm"' in result.stdout
    assert "3 entries" in result.stdout


def test_list_on_a_fresh_machine_invites_a_first_add(run):
    result = run("ledger", "list")
    assert result.returncode == 0
    assert "no entries yet in" in result.stdout
    assert "slakebind ledger add --species oak" in result.stdout


def test_list_totals_sums_per_species_in_one_unit(run):
    seed_three(run)
    result = run("ledger", "list", "--totals")
    assert result.returncode == 0
    assert "Totals by species (kg)" in result.stdout
    # oak: 25 kg + 8 lb (3.629 kg) = 28.629 kg ; birch: 12.5 kg
    assert "oak" in result.stdout and "28.629 kg" in result.stdout
    assert "12.5 kg" in result.stdout
    assert "all" in result.stdout and "41.129 kg" in result.stdout


def test_list_totals_honours_an_imperial_display_unit(run):
    seed_three(run)
    result = run("ledger", "list", "--totals", "--unit", "imperial")
    assert result.returncode == 0
    assert "Totals by species (lb)" in result.stdout


def test_list_rejects_a_bad_date_filter(run):
    result = run("ledger", "list", "--from", "01/01/2025")
    assert result.returncode != 0
    assert "--from" in combined(result)


# --------------------------------------------------------------------------
# ledger corruption guard and reset
# --------------------------------------------------------------------------


def test_a_corrupt_ledger_is_refused_and_left_untouched(run, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "ledger.json").write_text("this is not json", encoding="utf-8")
    before = (home / "ledger.json").read_bytes()
    result = run("ledger", "list")
    assert result.returncode != 0
    assert str(home / "ledger.json") in combined(result)
    assert "--reset" in combined(result)
    assert (home / "ledger.json").read_bytes() == before


def test_a_structurally_wrong_ledger_is_refused_too(run, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "ledger.json").write_text('{"hello": "world"}', encoding="utf-8")
    before = (home / "ledger.json").read_bytes()
    result = run("ledger", "list")
    assert result.returncode != 0
    assert (home / "ledger.json").read_bytes() == before


def test_reset_replaces_a_corrupt_ledger_with_an_empty_one(run, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "ledger.json").write_text("not json", encoding="utf-8")
    result = run("ledger", "list", "--reset")
    assert result.returncode == 0
    assert "reset" in result.stdout
    assert read_ledger(home) == {"version": 1, "entries": []}


def test_add_recovers_from_a_corrupt_ledger_with_reset(run, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "ledger.json").write_text("not json", encoding="utf-8")
    result = run("ledger", "add", "--species", "oak", "--qty", "5kg", "--reset")
    assert result.returncode == 0
    entries = read_ledger(home)["entries"]
    assert len(entries) == 1 and entries[0]["species"] == "oak"


def test_a_leftover_temp_file_does_not_break_the_next_run(run, home):
    run("ledger", "add", "--species", "birch", "--qty", "12.5kg", "--date", "2025-03-14")
    (home / ".ledger-half.tmp").write_text("{ truncated", encoding="utf-8")
    result = run("ledger", "list")
    assert result.returncode == 0
    assert "2025-03-14" in result.stdout
    assert len(read_ledger(home)["entries"]) == 1


# --------------------------------------------------------------------------
# ledger remove
# --------------------------------------------------------------------------


def add_one(run, species="oak", qty="25kg", date="2025-04-01"):
    output = run("ledger", "add", "--species", species, "--qty", qty, "--date", date).stdout
    added = next(line for line in output.splitlines() if line.startswith("added "))
    return added.split()[1]


def test_remove_refuses_without_a_terminal_unless_yes_is_given(run, home):
    entry_id = add_one(run)
    result = run("ledger", "remove", entry_id, stdin="y\n")
    assert result.returncode != 0
    assert "--yes" in combined(result)
    assert len(read_ledger(home)["entries"]) == 1


def test_remove_with_yes_deletes_only_that_entry(run, home):
    keep = add_one(run, species="birch", qty="12.5kg", date="2025-03-14")
    drop = add_one(run, species="oak", qty="25kg", date="2025-04-01")
    result = run("ledger", "remove", drop, "--yes")
    assert result.returncode == 0
    assert "Entry to remove" in result.stdout
    assert "removed {}".format(drop) in result.stdout
    remaining = read_ledger(home)["entries"]
    assert [e["id"] for e in remaining] == [keep]


def test_remove_of_an_unknown_id_changes_nothing(run, home):
    add_one(run)
    before = (home / "ledger.json").read_bytes()
    result = run("ledger", "remove", "deadbeef")
    assert result.returncode != 0
    assert "no such entry" in combined(result)
    assert (home / "ledger.json").read_bytes() == before


def test_remove_without_an_id_shows_usage(run):
    result = run("ledger", "remove")
    assert result.returncode != 0


def test_ledger_without_an_action_explains_the_actions(run):
    result = run("ledger")
    assert result.returncode != 0
    message = combined(result)
    assert "add" in message and "list" in message and "remove" in message


# --------------------------------------------------------------------------
# output hygiene
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ("ratio", "--hide", "8.5kg", "--tannin", "340g"),
        ("soak", "5m"),
        ("ledger", "list"),
        ("ledger", "list", "--totals"),
    ],
)
def test_piped_output_is_plain_ascii_with_no_escapes(run, args):
    result = run(*args)
    assert ANSI not in result.stdout
    assert result.stdout.isascii()


def test_no_color_environment_variable_removes_emphasis(run):
    result = run("ratio", "--hide", "8.5kg", "--tannin", "340g", env={"NO_COLOR": "1"})
    assert ANSI not in result.stdout


def test_results_go_to_stdout_and_errors_to_stderr(run):
    ok = run("ratio", "--hide", "8.5kg", "--tannin", "340g")
    assert "1:25" in ok.stdout and ok.stderr == ""
    bad = run("ratio", "--hide", "0kg", "--tannin", "340g")
    assert bad.stdout == "" and "hide weight" in bad.stderr


# --------------------------------------------------------------------------
# the interactive paths, driven through a real pseudo-terminal
# --------------------------------------------------------------------------


def spawn_in_pty(*args, home):
    env = dict(os.environ)
    env.pop("NO_COLOR", None)
    env["SLAKEBIND_HOME"] = str(home)
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        stdin=slave, stdout=slave, stderr=slave, env=env, cwd=str(ROOT), close_fds=True,
    )
    os.close(slave)
    return process, master


def drain(master, process, deadline, on_output=None):
    output = bytearray()
    while time.time() < deadline and process.poll() is None:
        ready, _w, _x = select.select([master], [], [], 0.3)
        if not ready:
            continue
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if on_output is not None:
            on_output(output)
    return bytes(output)


def read_remaining(master):
    output = bytearray()
    while True:
        ready, _w, _x = select.select([master], [], [], 0.3)
        if not ready:
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
    return bytes(output)


def test_soak_counts_down_then_alerts_until_a_key_is_pressed(home):
    process, master = spawn_in_pty("soak", "1s", home=home)
    acknowledged = {"done": False}

    def acknowledge(output):
        if b"SOAK DONE" in output and not acknowledged["done"]:
            os.write(master, b"x")
            acknowledged["done"] = True

    try:
        output = drain(master, process, time.time() + 25, on_output=acknowledge)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        else:
            process.wait(timeout=5)
        output += read_remaining(master)
        assert acknowledged["done"], output.decode("utf-8", "replace")
        assert b"\a" in output
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)


def test_soak_cancelled_with_ctrl_c_reports_and_exits_130(home):
    process, master = spawn_in_pty("soak", "2h", home=home)
    output = bytearray()
    started = time.time()
    sent = False
    try:
        while time.time() - started < 20:
            ready, _w, _x = select.select([master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
            if not sent and time.time() - started > 1.2:
                process.send_signal(signal.SIGINT)
                sent = True
            if process.poll() is not None:
                for _ in range(3):
                    ready, _w, _x = select.select([master], [], [], 0.2)
                    if not ready:
                        continue
                    try:
                        chunk = os.read(master, 4096)
                    except OSError:
                        break
                    if not chunk:
                        break
                    output.extend(chunk)
                break
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        assert process.returncode == 130
        assert b"Cancelled the soak" in bytes(output), bytes(output).decode("utf-8", "replace")
        assert b"Traceback" not in bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)


def test_a_terminal_run_uses_the_committed_accent_colour(home):
    process, master = spawn_in_pty("ratio", "--hide", "8.5kg", "--tannin", "340g", home=home)
    try:
        output = drain(master, process, time.time() + 10)
        process.wait(timeout=10)
        output += read_remaining(master)
        assert process.returncode == 0
        assert b"\x1b[38;2;0;141;160m" in output  # #008da0, the committed accent
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)


def test_remove_prompts_on_a_terminal_and_yes_answers_it(home):
    process, master = spawn_in_pty(
        "ledger", "add", "--species", "oak", "--qty", "25kg", "--date", "2025-04-01", home=home
    )
    process.wait(timeout=10)
    entry_id = ""
    for line in read_remaining(master).decode("utf-8", "replace").splitlines():
        if line.startswith("added "):
            entry_id = line.split()[1]
    os.close(master)
    assert entry_id

    process, master = spawn_in_pty("ledger", "remove", entry_id, home=home)
    answered = {"done": False}

    def answer(output):
        if b"[y/N]" in output and not answered["done"]:
            os.write(master, b"y\n")
            answered["done"] = True

    try:
        output = drain(master, process, time.time() + 10, on_output=answer)
        if process.poll() is None:
            process.wait(timeout=10)
        output += read_remaining(master)
        assert process.returncode == 0
        assert b"Entry to remove" in output
        assert b"removed" in output
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)
    assert json.loads((home / "ledger.json").read_text(encoding="utf-8"))["entries"] == []


def test_overview_screen_shows_the_ledger_on_hand(run):
    run("ledger", "add", "--species", "oak", "--qty", "25kg", "--date", "2025-04-01")
    result = run()
    assert result.returncode == 0
    assert "Your ledger" in result.stdout
    assert "25 kg" in result.stdout
    assert "on hand: 25 kg" in result.stdout


def test_overview_screen_survives_a_corrupt_ledger(run, home):
    home.mkdir(parents=True, exist_ok=True)
    (home / "ledger.json").write_text("not json", encoding="utf-8")
    result = run()
    assert result.returncode == 0
    assert "unreadable" in result.stdout
    assert (home / "ledger.json").read_text(encoding="utf-8") == "not json"
