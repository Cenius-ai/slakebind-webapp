# Example data for Slakebind

## `sample-ledger.json`

A ready-made bark source ledger with six entries across three species and three
months, in the exact shape Slakebind writes. Copy it into place to start with
data on hand:

```bash
mkdir -p ~/.slakebind
cp examples/sample-ledger.json ~/.slakebind/ledger.json
python3 slakebind.py ledger list --totals
```

You can also point `SLAKEBIND_HOME` at this folder to try the tool without
touching your home directory:

```bash
SLAKEBIND_HOME=examples python3 slakebind.py ledger list --totals
```

(That only works if the file is named `ledger.json`, so copy it first.)

## `soak-plans.md`

Three soaks the workshop actually runs, with the command for each.
