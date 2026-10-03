#!/usr/bin/env python3
"""Slakebind - a one-file tanning bench tool.

Three jobs, one file, standard library only:

  ratio    tannin-to-hide ratio from a hide weight and a tannin amount
  soak     live lime bath soak countdown with a terminal alert
  ledger   bark source ledger kept as JSON at ~/.slakebind/ledger.json

Nothing here talks to a network and there is no install step: copy the file and
run it with any Python 3.8+ interpreter.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import secrets
import select
import sys
import tempfile
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

PROG = "slakebind"
VERSION = "1.0.0"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_LEDGER = 3
EXIT_INTERRUPT = 130

# Design tokens - the single source of truth for this tool's terminal surface.
# Committed direction: calm-precise, terminal-dark, compact, system monospace.
#   accent   oklch(0.58 0.12 209) == #008da0 (truecolor 38;2;0;141;160).
#            One accent, only ever on the primary status, never the only signal.
#   neutral  the terminal's own foreground on its own background; no background
#            is ever painted, so contrast stays with the user's theme.
#   type     system monospace; columns separated by a fixed 2-space gutter.
#   density  compact: one blank line between blocks, never inside a block.
ACCENT_HEX = "#008da0"
ACCENT_RGB = "38;2;0;141;160"
BOLD = "1"
RESET = "\x1b[0m"
GUTTER = "  "

UNIT_GRAMS = {
    "g": Decimal("1"),
    "kg": Decimal("1000"),
    "oz": Decimal("28.349523125"),
    "lb": Decimal("453.59237"),
}

UNIT_ALIASES = {
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg",
    "kilogram": "kg", "kilograms": "kg",
    "oz": "oz", "ounce": "oz", "ounces": "oz",
    "lb": "lb", "lbs": "lb", "pound": "lb", "pounds": "lb",
}

# One hide is a few tens of kilos; this cap only stops a fat-fingered exponent
# from turning the output into a wall of digits.
MAX_GRAMS = Decimal("1000000000")  # 1000 tonnes of hide or of tannin
LEDGER_UNITS = ("g", "kg", "oz", "lb")


class InputError(Exception):
    """A message that is already user-facing and safe to print verbatim."""


class LedgerError(Exception):
    """A ledger read/write problem; the message names the file path."""


# --- terminal colour (auto-off for pipes, files and NO_COLOR) ---------------
class Palette:
    """Two emphasis levels, both no-ops when colour is disabled."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def accent(self, text: str) -> str:
        return "\x1b[{}m{}\x1b[{}m".format(ACCENT_RGB, text, RESET) if self.enabled else text

    def bold(self, text: str) -> str:
        return "\x1b[{}m{}\x1b[{}m".format(BOLD, text, RESET) if self.enabled else text


PALETTE = Palette(False)


def colour_enabled(stream, no_color_flag: bool) -> bool:
    if no_color_flag or os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


# --- formatting helpers -----------------------------------------------------
def trim(value: Decimal, places: int, min_places: int = 0) -> str:
    """Render a Decimal with at most `places` decimals and no trailing zeros."""
    try:
        text = format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")
    except InvalidOperation:
        return str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-"):
        text = "0"
    if min_places and "." not in text:
        text += "." + ("0" * min_places)
    return text


def fmt_duration(seconds: float) -> str:
    """Plain-words duration: 1h 30m, 45m, 12s, 2m 15s."""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}h {:02d}m {:02d}s".format(hours, minutes, secs) if secs else "{}h {:02d}m".format(hours, minutes)
    if minutes:
        return "{}m {:02d}s".format(minutes, secs) if secs else "{}m".format(minutes)
    return "{}s".format(secs)


def fmt_clock(seconds: float) -> str:
    """Countdown clock. Rounds up so the display never shows 00:00 early."""
    total = max(0, int(seconds + 0.999))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, secs)
    return "{:02d}:{:02d}".format(minutes, secs)


def local_pull_time(seconds: float) -> dt.datetime:
    return dt.datetime.now() + dt.timedelta(seconds=seconds)


def stamp(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def today_iso() -> str:
    return dt.date.today().isoformat()


def fail(message: str, code: int) -> int:
    sys.stdout.flush()
    print("{}: {}".format(PROG, message), file=sys.stderr)
    return code


# --- input parsing ----------------------------------------------------------
def parse_amount(raw: str, field: str, default_unit: str, allowed):
    """Read '8.5kg' / '340 g' / '18.7' into (value, canonical unit)."""
    text = (raw or "").strip().lower()
    if not text:
        raise InputError("{} is empty - give a number such as 8.5kg or 340g".format(field))
    cut = len(text)
    while cut > 0 and text[cut - 1].isalpha():
        cut -= 1
    number_text, unit_text = text[:cut].strip(), text[cut:].strip()
    if not number_text:
        raise InputError("{} {!r} is not a number - give a number such as 8.5kg or 340g".format(field, raw.strip()))
    if unit_text:
        unit = UNIT_ALIASES.get(unit_text)
        if unit is None:
            raise InputError("{} unit {!r} is not recognised - use one of {}".format(field, unit_text, ", ".join(allowed)))
    else:
        unit = default_unit
    if unit not in allowed:
        raise InputError("{} unit {} is not valid here - use one of {}".format(field, unit, ", ".join(allowed)))
    try:
        value = Decimal(number_text)
    except InvalidOperation:
        raise InputError("{} {!r} is not a number - give a number such as 8.5kg or 340g".format(field, raw.strip()))
    if not value.is_finite():
        raise InputError("{} {!r} is not a finite number".format(field, raw.strip()))
    if value <= 0:
        raise InputError("{} must be greater than zero (got {})".format(field, raw.strip()))
    if value * UNIT_GRAMS[unit] > MAX_GRAMS:
        raise InputError("{} is unreasonably large (max 1000 tonnes) - got {}".format(field, raw.strip()))
    return value, unit


DURATION_UNITS = {
    "": Decimal(60),  # a bare number is minutes
    "s": Decimal(1), "sec": Decimal(1), "secs": Decimal(1), "second": Decimal(1), "seconds": Decimal(1),
    "m": Decimal(60), "min": Decimal(60), "mins": Decimal(60), "minute": Decimal(60), "minutes": Decimal(60),
    "h": Decimal(3600), "hr": Decimal(3600), "hrs": Decimal(3600), "hour": Decimal(3600), "hours": Decimal(3600),
}


def _duration_hint(raw: str) -> str:
    return ("soak duration {!r} is not a number of minutes or a 1h30m style duration - "
            "use 90 (minutes), 90m, 1h30m or 45s".format((raw or "").strip()))


def parse_duration(raw: str) -> float:
    """Read '90m', '1h30m', '45s' or a bare minute count into seconds."""
    text = (raw or "").strip().lower().replace(" ", "")
    if not text:
        raise InputError("soak duration is empty - give minutes (90) or a 1h30m style duration")
    total = Decimal(0)
    number, unit, saw_token = "", "", False

    def flush_token() -> None:
        nonlocal total, number, unit, saw_token
        if not number:
            return
        multiplier = DURATION_UNITS.get(unit)
        if multiplier is None:
            raise InputError(_duration_hint(raw))
        try:
            value = Decimal(number)
        except InvalidOperation:
            raise InputError(_duration_hint(raw))
        if not value.is_finite():
            raise InputError(_duration_hint(raw))
        total += value * multiplier
        saw_token = True
        number, unit = "", ""

    for char in text:
        if char.isdigit() or char == ".":
            if unit:
                flush_token()
            number += char
        elif char.isalpha():
            if not number:
                raise InputError(_duration_hint(raw))
            unit += char
        else:
            raise InputError(_duration_hint(raw))
    flush_token()
    if not saw_token or total <= 0:
        raise InputError(_duration_hint(raw))
    return float(total)


# --- ratio ------------------------------------------------------------------
RATIO_EXAMPLES = """\
examples:
  slakebind ratio --hide 8.5kg --tannin 340g
  slakebind ratio --hide 18.7 --tannin 12oz --unit imperial
  slakebind ratio --hide 10kg --tannin 400g

A unitless --hide is read as kg (metric) or lb (imperial); a unitless --tannin as
g (metric) or oz (imperial). An inline unit always wins."""


@dataclass(frozen=True)
class RatioResult:
    hide_value: Decimal
    hide_unit: str
    hide_grams: Decimal
    tannin_value: Decimal
    tannin_unit: str
    tannin_grams: Decimal
    percent: Decimal
    ratio: Decimal


def compute_ratio(hide_value: Decimal, hide_unit: str, tannin_value: Decimal, tannin_unit: str) -> RatioResult:
    hide_grams = hide_value * UNIT_GRAMS[hide_unit]
    tannin_grams = tannin_value * UNIT_GRAMS[tannin_unit]
    return RatioResult(
        hide_value, hide_unit, hide_grams, tannin_value, tannin_unit, tannin_grams,
        tannin_grams / hide_grams * Decimal(100), hide_grams / tannin_grams,
    )


def cmd_ratio(args) -> int:
    if not args.hide or not args.tannin:
        return fail("ratio needs a hide weight and a tannin amount.\n"
                    "  slakebind ratio --hide 8.5kg --tannin 340g\n"
                    "  slakebind ratio --hide 18.7 --tannin 12oz --unit imperial", EXIT_USAGE)
    metric = args.unit == "metric"
    try:
        hide_value, hide_unit = parse_amount(args.hide, "hide weight", "kg" if metric else "lb", ("kg", "lb"))
        tannin_value, tannin_unit = parse_amount(args.tannin, "tannin amount", "g" if metric else "oz", ("g", "oz"))
    except InputError as exc:
        return fail(str(exc), EXIT_USAGE)

    result = compute_ratio(hide_value, hide_unit, tannin_value, tannin_unit)
    hide_alt = result.hide_grams / UNIT_GRAMS["lb" if hide_unit == "kg" else "kg"]
    tannin_alt = result.tannin_grams / UNIT_GRAMS["oz" if tannin_unit == "g" else "g"]
    percent_text = trim(result.percent, 2, min_places=1)
    ratio_text = trim(result.ratio, 2)
    hide_g, tannin_g = trim(result.hide_grams, 6), trim(result.tannin_grams, 6)
    share = trim(result.tannin_grams / result.hide_grams, 4)

    print(PALETTE.bold("Slakebind ratio"))
    print("Hide weight : {} {} ({} {})".format(
        trim(result.hide_value, 4), hide_unit, trim(hide_alt, 4), "lb" if hide_unit == "kg" else "kg"))
    print("Tannin      : {} {} ({} {})".format(
        trim(result.tannin_value, 4), tannin_unit, trim(tannin_alt, 4), "oz" if tannin_unit == "g" else "g"))
    print("Tannin share: {}% of hide weight".format(percent_text))
    print("Ratio       : {} (1 part tannin to {} parts hide)".format(PALETTE.accent("1:" + ratio_text), ratio_text))
    print("Math        : {} g tannin / {} g hide = {}  ->  {}%".format(tannin_g, hide_g, share, percent_text))
    print("Math        : {} g hide / {} g tannin = {}".format(hide_g, tannin_g, ratio_text))
    return EXIT_OK


# --- soak -------------------------------------------------------------------
SOAK_EXAMPLES = """\
examples:
  slakebind soak 90m                 count down 90 minutes in the terminal
  slakebind soak 1h30m               the same soak written the long way
  slakebind soak 45s                 a short test soak
  slakebind soak 90m --no-wait       print the pull time and exit at once

A bare number is read as minutes. When stdout is not a terminal (a pipe or a log
file) the countdown degrades to a single target-time line and exits."""


def soak_target_line(seconds: float) -> str:
    return "soak {}  ->  pull at {}  ({} from now)".format(
        fmt_duration(seconds), stamp(local_pull_time(seconds)), fmt_duration(seconds))


def make_cbreak():
    """Put stdin in cbreak mode so one keypress is readable without Enter."""
    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover - non-POSIX
        return None
    try:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except (OSError, ValueError, termios.error):  # type: ignore[attr-defined]
        return None

    def restore() -> None:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (OSError, ValueError, termios.error):  # type: ignore[attr-defined]
            pass

    return restore


def wait_for_key(timeout: float) -> bool:
    try:
        ready, _w, _x = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):
        return False
    if not ready:
        return False
    try:
        sys.stdin.read(1)
    except (OSError, ValueError):
        pass
    return True


def alert_done(total: float, pull: str) -> int:
    """BEL plus a DONE line that repeats until a key or Ctrl-C, which returns 0."""
    message = PALETTE.bold("SOAK DONE") + " - {} soak finished (pull time {}). ".format(
        fmt_duration(total), pull) + "Pull the hide, then press any key."
    restore = make_cbreak()
    try:
        while True:
            sys.stdout.write("\r\a" + message + "  ")
            sys.stdout.flush()
            try:
                if wait_for_key(2.0):
                    break
            except KeyboardInterrupt:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if restore is not None:
            restore()
    print()
    print("Soak complete: {} finished at {}.".format(PALETTE.accent(fmt_duration(total)), pull))
    return EXIT_OK


def run_countdown(total: float) -> int:
    """Live countdown, then the repeating DONE alert. Returns an exit code."""
    deadline = time.monotonic() + total
    pull = stamp(local_pull_time(total))
    width = 0
    print("soak {} (pull at {}) - Ctrl-C cancels".format(fmt_duration(total), pull))
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            line = "  remaining {}   elapsed {}   ".format(fmt_clock(remaining), fmt_clock(total - remaining))
            sys.stdout.write("\r" + line.ljust(width))
            width = max(width, len(line))
            sys.stdout.flush()
            time.sleep(min(1.0, max(0.05, remaining)))
    except KeyboardInterrupt:
        remaining = max(0.0, deadline - time.monotonic())
        sys.stdout.write("\r" + " " * width + "\r")
        sys.stdout.flush()
        print()
        print("Cancelled the soak: {} elapsed, {} still to go.".format(
            fmt_duration(max(0.0, total - remaining)), fmt_duration(remaining)))
        return EXIT_INTERRUPT
    sys.stdout.write("\r" + " " * width + "\r")
    sys.stdout.flush()
    return alert_done(total, pull)


def cmd_soak(args) -> int:
    if not args.duration:
        return fail("soak needs a duration.\n  slakebind soak 90m\n  slakebind soak 1h30m\n"
                    "  slakebind soak 90m --no-wait", EXIT_USAGE)
    try:
        seconds = parse_duration(args.duration)
    except InputError as exc:
        return fail(str(exc), EXIT_USAGE)
    if args.no_wait or not sys.stdout.isatty():
        print(soak_target_line(seconds))
        return EXIT_OK
    return run_countdown(seconds)


# --- ledger storage ---------------------------------------------------------
def ledger_home() -> str:
    override = os.environ.get("SLAKEBIND_HOME", "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(os.path.expanduser("~"), ".slakebind")


def ledger_path() -> str:
    return os.path.join(ledger_home(), "ledger.json")


def empty_ledger() -> dict:
    return {"version": 1, "entries": []}


def load_ledger(path: str, reset: bool = False) -> dict:
    """Read the ledger. Never writes except for an explicit --reset."""
    if not os.path.exists(path):
        return empty_ledger()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise LedgerError("cannot read the ledger at {} ({})".format(path, exc.strerror or exc))
    if not raw.strip():
        return empty_ledger()
    if reset:
        save_ledger(empty_ledger(), path)
        print("reset {}: {} bytes of ledger replaced with an empty ledger".format(path, len(raw.encode("utf-8"))))
        return empty_ledger()
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise LedgerError("the ledger at {} is not valid JSON ({}); nothing was changed - "
                          "re-run with --reset to replace it".format(path, exc))
    if not isinstance(document, dict) or not isinstance(document.get("entries"), list):
        raise LedgerError("the ledger at {} is not a Slakebind ledger (expected an object with an "
                          "entries list); nothing was changed - re-run with --reset to replace it".format(path))
    for index, entry in enumerate(document["entries"]):
        if not isinstance(entry, dict) or not {"id", "species", "bath_date", "quantity", "unit"} <= set(entry):
            raise LedgerError("the ledger at {} has a damaged entry at position {}; nothing was changed - "
                              "re-run with --reset to replace it".format(path, index + 1))
    return document


def save_ledger(document, path: str) -> None:
    """Atomic write: temp file in the same directory, then os.replace."""
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise LedgerError("cannot create {} ({})".format(directory, exc.strerror or exc))
    handle = tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".ledger-", suffix=".tmp", dir=directory)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        json.dump(document, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_path, path)
        tmp_path = None
    except OSError as exc:
        raise LedgerError("cannot write {} ({})".format(path, exc.strerror or exc))
    finally:
        if handle is not None:
            handle.close()
        if tmp_path is not None and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def new_id(existing) -> str:
    taken = {entry.get("id") for entry in existing}
    while True:
        candidate = secrets.token_hex(4)
        if candidate not in taken:
            return candidate


def make_entry(species: str, source: str, quantity: Decimal, unit: str, bath_date: str, note: str, taken) -> dict:
    return {
        "id": new_id(taken),
        "created_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "bath_date": bath_date,
        "species": species,
        "source": source,
        "quantity": trim(quantity, 6),
        "unit": unit,
        "quantity_g": trim(quantity * UNIT_GRAMS[unit], 6),
        "note": note,
    }


def entry_grams(entry: dict) -> Decimal:
    try:
        return Decimal(str(entry.get("quantity_g", "0")))
    except InvalidOperation:
        return Decimal(0)


def parse_iso_date(raw: str, field: str) -> str:
    try:
        return dt.date.fromisoformat((raw or "").strip()).isoformat()
    except ValueError:
        raise InputError("{} {!r} is not a YYYY-MM-DD date".format(field, raw))


# --- ledger add -------------------------------------------------------------
ADD_EXAMPLES = """\
examples:
  slakebind ledger add --species oak --source "Hill Farm" --qty 25kg
  slakebind ledger add --species birch --qty 12.5kg --date 2025-03-14 --note "stand behind the barn"
  slakebind ledger add --species oak --qty 25 --unit imperial

--qty takes an inline unit (25kg, 12oz). Without one the --unit flag decides
(metric -> kg, imperial -> lb). --date defaults to today. --reset replaces a
damaged ledger file with an empty one first."""


def cmd_ledger_add(args) -> int:
    try:
        path = ledger_path()
        document = load_ledger(path, reset=args.reset)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)

    species = (args.species or "").strip()
    if not species:
        return fail("species must not be empty - try --species oak", EXIT_USAGE)
    try:
        quantity, unit = parse_amount(args.qty or "", "quantity", "kg" if args.unit == "metric" else "lb", LEDGER_UNITS)
        bath_date = parse_iso_date(args.date, "bath date") if args.date else today_iso()
    except InputError as exc:
        return fail(str(exc), EXIT_USAGE)

    existed = os.path.exists(path)
    entry = make_entry(species, (args.source or "").strip(), quantity, unit, bath_date,
                       (args.note or "").strip(), document["entries"])
    document["entries"].append(entry)
    try:
        save_ledger(document, path)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)

    if not existed:
        print("created {}".format(ledger_home()))
    parts = ["added {}".format(entry["id"]), species, "{} {}".format(trim(quantity, 6), unit)]
    if entry["source"]:
        parts.append("from {}".format(entry["source"]))
    parts.append("bath date {}".format(bath_date))
    if entry["note"]:
        parts.append("note {}".format(entry["note"]))
    print("  ".join(parts))
    count = len(document["entries"])
    print("saved to {} ({} {})".format(path, count, "entry" if count == 1 else "entries"))
    if not existed:
        print("next: slakebind ledger list")
    return EXIT_OK


# --- ledger list and totals -------------------------------------------------
LIST_EXAMPLES = """\
examples:
  slakebind ledger list
  slakebind ledger list --species oak --from 2025-01-01 --to 2025-06-30
  slakebind ledger list --totals
  slakebind ledger list --reset          replace a damaged ledger with an empty one

--from/--to are inclusive YYYY-MM-DD bounds on the bath date. --species is a
case-insensitive substring match on the bark species."""


def filter_entries(entries, species: str = "", date_from: str = "", date_to: str = ""):
    chosen = entries
    if species:
        needle = species.strip().lower()
        chosen = [e for e in chosen if needle in str(e.get("species", "")).lower()]
    if date_from:
        chosen = [e for e in chosen if str(e.get("bath_date", "")) >= date_from]
    if date_to:
        chosen = [e for e in chosen if str(e.get("bath_date", "")) <= date_to]
    return chosen


def filter_label(species: str, date_from: str, date_to: str) -> str:
    parts = []
    if species:
        parts.append('species contains "{}"'.format(species.strip()))
    if date_from:
        parts.append("from {}".format(date_from))
    if date_to:
        parts.append("to {}".format(date_to))
    return ", ".join(parts)


def newest_first(entries):
    return sorted(entries, key=lambda e: (str(e.get("bath_date", "")), str(e.get("created_at", "")),
                                          str(e.get("id", ""))), reverse=True)


def print_table(entries) -> None:
    headers = ["ID", "BATH DATE", "SPECIES", "SOURCE", "QTY", "NOTE"]
    rows = []
    for entry in entries:
        note = str(entry.get("note", "")) or "-"
        rows.append([
            str(entry.get("id", "")),
            str(entry.get("bath_date", "")),
            str(entry.get("species", "")),
            str(entry.get("source", "")) or "-",
            "{} {}".format(entry.get("quantity", ""), entry.get("unit", "")),
            note if len(note) <= 40 else note[:37] + "...",
        ])
    widths = [max([len(headers[i])] + [len(row[i]) for row in rows]) for i in range(len(headers))]
    print(GUTTER.join(headers[i].ljust(widths[i]) for i in range(len(headers))).rstrip())
    print("-" * (sum(widths) + len(GUTTER) * (len(headers) - 1)))
    for row in rows:
        print(GUTTER.join(row[i].ljust(widths[i]) for i in range(len(headers))).rstrip())


def print_totals(entries, unit: str) -> None:
    buckets = {}
    for entry in entries:
        species = str(entry.get("species", "")) or "(unnamed)"
        bucket = buckets.setdefault(species, {"grams": Decimal(0), "count": 0})
        bucket["grams"] += entry_grams(entry)
        bucket["count"] += 1
    names = sorted(buckets)
    rows = [["{} {}".format(trim(buckets[name]["grams"] / UNIT_GRAMS[unit], 3), unit),
             "{} {}".format(buckets[name]["count"], "entry" if buckets[name]["count"] == 1 else "entries")]
            for name in names]
    grand = sum((bucket["grams"] for bucket in buckets.values()), Decimal(0))
    total = sum(bucket["count"] for bucket in buckets.values())
    rows.append(["{} {}".format(trim(grand / UNIT_GRAMS[unit], 3), unit),
                 "{} {}".format(total, "entry" if total == 1 else "entries")])
    name_width = max([len(name) for name in names] + [len("all")])
    qty_width = max(len(row[0]) for row in rows)
    count_width = max(len(row[1]) for row in rows)
    print()
    print(PALETTE.bold("Totals by species ({})".format(unit)))
    for index, name in enumerate(names):
        print(GUTTER.join([name.ljust(name_width), rows[index][0].ljust(qty_width), rows[index][1]]).rstrip())
    print("-" * (name_width + qty_width + count_width + len(GUTTER) * 2))
    print(GUTTER.join(["all".ljust(name_width), rows[-1][0].ljust(qty_width), rows[-1][1]]).rstrip())


def cmd_ledger_list(args) -> int:
    try:
        date_from = parse_iso_date(args.date_from, "--from") if args.date_from else ""
        date_to = parse_iso_date(args.date_to, "--to") if args.date_to else ""
    except InputError as exc:
        return fail(str(exc), EXIT_USAGE)
    try:
        path = ledger_path()
        document = load_ledger(path, reset=args.reset)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)

    entries = newest_first(document["entries"])
    chosen = filter_entries(entries, args.species or "", date_from, date_to)
    if not chosen:
        if not entries:
            print("no entries yet in {}".format(path))
            print("start one with: slakebind ledger add --species oak --source \"Hill Farm\" --qty 25kg")
        else:
            print("no entries match ({})".format(filter_label(args.species or "", date_from, date_to)))
            print("there {} {} in the ledger; run slakebind ledger list to see them all".format(
                "is" if len(entries) == 1 else "are",
                "1 entry" if len(entries) == 1 else "{} entries".format(len(entries))))
        return EXIT_OK

    print_table(chosen)
    label = filter_label(args.species or "", date_from, date_to)
    print("{} listed{}".format(
        "1 entry" if len(chosen) == 1 else "{} entries".format(len(chosen)),
        " ({})".format(label) if label else ""))
    if args.totals:
        print_totals(chosen, "kg" if args.unit == "metric" else "lb")
    return EXIT_OK


# --- ledger remove ----------------------------------------------------------
REMOVE_EXAMPLES = """\
examples:
  slakebind ledger remove 3f9a1c2e
  slakebind ledger remove 3f9a1c2e --yes      skip the confirmation prompt

The entry is printed before anything is deleted, and the ledger is only
rewritten after you answer y. In a pipe you must pass --yes."""


def print_entry_block(entry: dict) -> None:
    fields = [
        ("id", str(entry.get("id", ""))),
        ("bath", str(entry.get("bath_date", ""))),
        ("species", str(entry.get("species", ""))),
        ("source", str(entry.get("source", "")) or "-"),
        ("qty", "{} {}".format(entry.get("quantity", ""), entry.get("unit", ""))),
        ("note", str(entry.get("note", "")) or "-"),
    ]
    for label, value in fields:
        print("{}{} {}".format(GUTTER, (label + ":").ljust(9), value))


def cmd_ledger_remove(args) -> int:
    try:
        path = ledger_path()
        document = load_ledger(path, reset=args.reset)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)

    wanted = (args.id or "").strip()
    match = next((e for e in document["entries"] if str(e.get("id", "")).lower() == wanted.lower()), None)
    if match is None:
        return fail("no such entry {!r} in {}".format(wanted, path), EXIT_USAGE)

    print("Entry to remove")
    print_entry_block(match)
    if not args.yes:
        if not sys.stdin.isatty():
            return fail("removing an entry needs a terminal for the confirmation, or --yes "
                        "(input is not a terminal here)", EXIT_USAGE)
        try:
            answer = input("remove this entry? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("nothing removed")
            return EXIT_OK

    document["entries"] = [e for e in document["entries"] if str(e.get("id", "")) != match.get("id")]
    try:
        save_ledger(document, path)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)
    print("removed {}  {}  {} {}  bath date {}".format(
        match.get("id", ""), match.get("species", ""),
        match.get("quantity", ""), match.get("unit", ""), match.get("bath_date", "")))
    return EXIT_OK


# --- argument parsing -------------------------------------------------------
class SlakebindParser(argparse.ArgumentParser):
    """ArgumentParser that also lets '-4kg' through as a value, not as a flag.

    Without this, '--hide -4kg' is rejected as an unknown option and the user
    never sees the message that names the field they got wrong.
    """

    _NEGATIVE_VALUE = re.compile(r"^-\d")

    def _parse_optional(self, arg_string):
        if arg_string and self._NEGATIVE_VALUE.match(arg_string):
            return None
        return super()._parse_optional(arg_string)


COMMON = SlakebindParser(add_help=False)
COMMON.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS,
                    help="never emit ANSI colour (NO_COLOR is honoured too)")

OVERVIEW_EXAMPLES = """\
examples:
  slakebind ratio --hide 8.5kg --tannin 340g
  slakebind ratio --hide 18.7 --tannin 12oz --unit imperial
  slakebind soak 1h30m
  slakebind soak 90m --no-wait
  slakebind ledger add --species oak --source "Hill Farm" --qty 25kg --note "spring bark"
  slakebind ledger list --totals
  slakebind ledger list --species oak --from 2025-01-01 --to 2025-06-30
  slakebind ledger remove 3f9a1c2e

The ledger lives at ~/.slakebind/ledger.json (override the folder with
SLAKEBIND_HOME). Run 'slakebind <command> --help' for one command's options."""

COMMANDS = [
    ("ratio", "tannin-to-hide ratio from a hide weight and a tannin amount"),
    ("soak", "live lime bath soak countdown with a terminal alert"),
    ("ledger add", "record a bark source and quantity"),
    ("ledger list", "list entries newest-first, with filters and --totals"),
    ("ledger remove", "delete one entry by id after printing it"),
]


def add_command(parsers, name, summary, description, examples):
    return parsers.add_parser(name, parents=[COMMON], help=summary, description=description,
                              epilog=examples, formatter_class=argparse.RawDescriptionHelpFormatter)


def build_parser() -> argparse.ArgumentParser:
    parser = SlakebindParser(
        prog=PROG, parents=[COMMON],
        description="Tannin-to-hide ratio, lime bath soak timer and bark source ledger.",
        epilog=OVERVIEW_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="{} {}".format(PROG, VERSION))
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    ratio = add_command(
        commands, "ratio", COMMANDS[0][1],
        "Compute the tannin-to-hide ratio, the tannin share of the hide weight and a "
        "hand-checkable arithmetic breakdown.", RATIO_EXAMPLES)
    ratio.add_argument("--hide", metavar="WEIGHT", help="hide weight, e.g. 8.5kg or 18.7 (unitless follows --unit)")
    ratio.add_argument("--tannin", metavar="AMOUNT", help="tannin amount, e.g. 340g or 12oz (unitless follows --unit)")
    ratio.add_argument("--unit", choices=("metric", "imperial"), default="metric",
                       help="which unit prints first, and how a unitless number is read (default: metric)")
    ratio.set_defaults(handler=cmd_ratio)

    soak = add_command(commands, "soak", COMMANDS[1][1],
                       "Run a live lime bath soak countdown that alerts when the soak is done. When stdout "
                       "is not a terminal it prints one target-time line instead.", SOAK_EXAMPLES)
    soak.add_argument("duration", nargs="?", metavar="DURATION", help="soak length: 90 (minutes), 90m, 1h30m or 45s")
    soak.add_argument("--no-wait", action="store_true",
                      help="print the absolute pull time and exit without counting down")
    soak.set_defaults(handler=cmd_soak)

    ledger = commands.add_parser("ledger", parents=[COMMON], help="record, list and remove bark sources",
                                 description="Keep a bark source ledger in a JSON file that Slakebind creates "
                                             "and rewrites atomically.")
    actions = ledger.add_subparsers(dest="ledger_command", metavar="<action>")

    ledger_add = add_command(actions, "add", COMMANDS[2][1], "Append one bark source entry to the ledger.",
                             ADD_EXAMPLES)
    ledger_add.add_argument("--species", metavar="NAME", help="bark species, e.g. oak")
    ledger_add.add_argument("--source", metavar="NAME", default="", help="supplier, stand or workshop")
    ledger_add.add_argument("--qty", metavar="AMOUNT", help="quantity with unit, e.g. 25kg or 12oz")
    ledger_add.add_argument("--date", metavar="YYYY-MM-DD", default="", help="bath date (default: today)")
    ledger_add.add_argument("--note", metavar="TEXT", default="", help="free-text note")
    ledger_add.add_argument("--unit", choices=("metric", "imperial"), default="metric",
                            help="how a unitless --qty is read (default: metric, i.e. kg)")
    ledger_add.add_argument("--reset", action="store_true",
                            help="replace a damaged ledger file with an empty one before adding")
    ledger_add.set_defaults(handler=cmd_ledger_add)

    ledger_list = add_command(actions, "list", COMMANDS[3][1],
                              "List ledger entries newest-first in aligned columns.", LIST_EXAMPLES)
    ledger_list.add_argument("--species", metavar="SUBSTRING", default="",
                             help="only entries whose species contains this text")
    ledger_list.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", default="",
                             help="inclusive earliest bath date")
    ledger_list.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", default="",
                             help="inclusive latest bath date")
    ledger_list.add_argument("--totals", action="store_true",
                             help="also print quantity-on-hand totals per species")
    ledger_list.add_argument("--unit", choices=("metric", "imperial"), default="metric",
                             help="unit for the totals block (default: metric, i.e. kg)")
    ledger_list.add_argument("--reset", action="store_true",
                             help="replace a damaged ledger file with an empty one")
    ledger_list.set_defaults(handler=cmd_ledger_list)

    ledger_remove = add_command(actions, "remove", COMMANDS[4][1],
                                "Delete one ledger entry by id after showing it and confirming.", REMOVE_EXAMPLES)
    ledger_remove.add_argument("id", nargs="?", metavar="ID", help="the short id shown by ledger list")
    ledger_remove.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ledger_remove.add_argument("--reset", action="store_true",
                               help="replace a damaged ledger file with an empty one first")
    ledger_remove.set_defaults(handler=cmd_ledger_remove)
    return parser


def print_overview() -> None:
    """The help-first screen: every command plus one copy-pasteable example."""
    print("{} {} - tannin ratio, lime bath soak timer and bark source ledger.".format(PROG, VERSION))
    print()
    print("usage: {} <command> [options]".format(PROG))
    print()
    print("Commands")
    width = max(len(name) for name, _text in COMMANDS)
    for name, text in COMMANDS:
        print("{}{}{}".format(GUTTER, name.ljust(width + 2), text))
    print()
    print(OVERVIEW_EXAMPLES)
    print()
    print_ledger_summary()
    print()
    print("'{} --version' prints the version; '{} <command> --help' explains one command.".format(PROG, PROG))


def print_ledger_summary() -> None:
    """A glance at the ledger on the overview screen, the way `git` shows status."""
    print("Your ledger")
    path = ledger_path()
    try:
        document = load_ledger(path)
    except LedgerError:
        print("{}{}  (unreadable - run 'slakebind ledger list --reset' to start again)".format(GUTTER, path))
        return
    entries = newest_first(document["entries"])
    if not entries:
        print("{}{}  (no entries yet)".format(GUTTER, path))
        print("{}start one with: slakebind ledger add --species oak --source \"Hill Farm\" --qty 25kg".format(GUTTER))
        return
    print("{}{} - {} {}".format(GUTTER, path, len(entries), "entry" if len(entries) == 1 else "entries"))
    print_table(entries[:3])
    if len(entries) > 3:
        print("{}... and {} more; run 'slakebind ledger list' for all of them".format(GUTTER, len(entries) - 3))
    on_hand = sum((entry_grams(entry) for entry in entries), Decimal(0))
    species = len({str(entry.get("species", "")) for entry in entries})
    print("{}on hand: {} kg across {} {}".format(
        GUTTER, trim(on_hand / UNIT_GRAMS["kg"], 3), species, "species" if species != 1 else "species"))


def main(argv=None) -> int:
    parser = build_parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(arguments)
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (EXIT_USAGE if code else EXIT_OK)

    global PALETTE
    PALETTE = Palette(colour_enabled(sys.stdout, getattr(args, "no_color", False)))

    handler = getattr(args, "handler", None)
    if handler is None:
        if getattr(args, "command", None) == "ledger":
            print("{} ledger needs an action: add, list or remove.".format(PROG), file=sys.stderr)
            print("  {} ledger list".format(PROG), file=sys.stderr)
            print("  {} ledger add --species oak --qty 25kg".format(PROG), file=sys.stderr)
            return EXIT_USAGE
        print_overview()
        return EXIT_OK

    try:
        return handler(args)
    except InputError as exc:
        return fail(str(exc), EXIT_USAGE)
    except LedgerError as exc:
        return fail(str(exc), EXIT_LEDGER)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return EXIT_INTERRUPT
    except BrokenPipeError:
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
