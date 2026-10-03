# Installing Slakebind

Slakebind is one Python file with no dependencies. There is nothing to compile,
no service to run and no database to create.

## 1. Prerequisites

Install these on the machine yourself - Slakebind's setup script never touches
system packages:

- **Python 3.8 or newer** (`python3 --version`). That is the whole list.

No `pip` packages are required: the tool imports only the standard library.

## 2. Unpack

```bash
tar xzf slakebind.tar.gz
cd slakebind
```

## 3. Install

### Option A - copy and run (nothing installed)

```bash
python3 slakebind.py --help
```

Slakebind is designed to install by copy: drop `slakebind.py` in any folder you
like and run it from there.

### Option B - put the `slakebind` command on your PATH

```bash
bash install.sh
```

`install.sh` refreshes pip's tooling, installs the project itself in editable
mode (`pip install -e .`, no dependencies), verifies that the module imports
and that one ratio command runs, then exits. It never starts a server and
never needs `sudo`. It is safe to run again.

## 4. Verify

```bash
python3 slakebind.py --version          # slakebind 1.0.0
python3 slakebind.py ratio --hide 8.5kg --tannin 340g
```

Expected ratio output - the tannin is 4.0% of the hide weight at 1:25:

```
Slakebind ratio
Hide weight : 8.5 kg (18.7393 lb)
Tannin      : 340 g (11.9931 oz)
Tannin share: 4.0% of hide weight
Ratio       : 1:25 (1 part tannin to 25 parts hide)
Math        : 340 g tannin / 8500 g hide = 0.04  ->  4.0%
Math        : 8500 g hide / 340 g tannin = 25
```

## 5. Optional: start with example data

```bash
mkdir -p ~/.slakebind
cp examples/sample-ledger.json ~/.slakebind/ledger.json
python3 slakebind.py ledger list --totals
```

## 6. Run the demo

`demo.sh` exercises every command end to end against a throwaway ledger, so it
never touches your real `~/.slakebind`:

```bash
bash demo.sh
```

## 7. Run the tests

```bash
python3 -m pytest
```

`pytest` is the only test-time requirement (`pip install -r requirements-dev.txt`
if your machine does not already have it). The tool itself still needs nothing.

## Configuration

Slakebind needs no configuration and reads no secrets. The single optional
environment variable is:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SLAKEBIND_HOME` | `~/.slakebind` | folder holding `ledger.json` |

Copy `.env.example` if you want a reminder; leaving `SLAKEBIND_HOME` unset (or
empty) is the normal case.

## Uninstall

Delete `slakebind.py` (and `~/.slakebind` if you want the ledger gone too). If
you ran `install.sh`, also remove the `slakebind` console script with
`pip uninstall slakebind`.
