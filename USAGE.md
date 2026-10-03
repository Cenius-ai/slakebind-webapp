# Using Slakebind

Three worked passes over the tool. Every command below is copy-pasteable, and
every block of output is what the tool actually prints.

## Pass 1: dose a hide

You have an 8.5 kg hide and 340 g of ground oak bark.

```bash
python3 slakebind.py ratio --hide 8.5kg --tannin 340g
```

```
Slakebind ratio
Hide weight : 8.5 kg (18.7393 lb)
Tannin      : 340 g (11.9931 oz)
Tannin share: 4.0% of hide weight
Ratio       : 1:25 (1 part tannin to 25 parts hide)
Math        : 340 g tannin / 8500 g hide = 0.04  ->  4.0%
Math        : 8500 g hide / 340 g tannin = 25
```

The last two lines are the point of the tool: 8.5 kg is 8500 g, 340 g of tannin
against 8500 g of hide is 4.0%, and one part tannin to twenty-five parts hide.
You can redo that on paper.

Working in pounds and ounces, and unsure of the dose:

```bash
python3 slakebind.py ratio --hide 18.7 --tannin 12oz --unit imperial
```

```
Slakebind ratio
Hide weight : 18.7 lb (8.4822 kg)
Tannin      : 12 oz (340.1943 g)
Tannin share: 4.01% of hide weight
Ratio       : 1:24.93 (1 part tannin to 24.93 parts hide)
Math        : 340.194278 g tannin / 8482.177319 g hide = 0.0401  ->  4.01%
Math        : 8482.177319 g hide / 340.194278 g tannin = 24.93
```

The unitless `18.7` was read as pounds because `--unit imperial` was passed.
`4.01%` is the answer; there is nothing between you and the arithmetic.

### When the input is wrong

```bash
python3 slakebind.py ratio --hide 0kg --tannin 340g
# slakebind: hide weight must be greater than zero (got 0kg)      [exit 2]

python3 slakebind.py ratio --hide abc --tannin 340g
# slakebind: hide weight 'abc' is not a number - give a number such as 8.5kg or 340g
```

One sentence, naming the field, no traceback, non-zero exit - safe to use in a
shell script.

## Pass 2: run a lime bath soak

Before committing a real hide, prove the alert works:

```bash
python3 slakebind.py soak 45s
```

The terminal shows one line that counts down once per second:

```
soak 45s (pull at 2025-04-01 14:37:12) - Ctrl-C cancels
  remaining 00:12   elapsed 00:33
```

then the bell and a **SOAK DONE** line with the pull time, repeating until you
press a key. Exit status 0.

The everyday soak:

```bash
python3 slakebind.py soak 1h30m
```

`90m`, `90`, `5400s` and `1h30m` all mean the same thing - a bare number is
minutes. If you press `Ctrl-C` while it runs, Slakebind stops, tells you how
much of the soak was left, and exits 130:

```
Cancelled the soak: 12m 08s elapsed, 1h 17m 52s still to go.
```

Going away from the bench? Print the pull time and close the terminal:

```bash
python3 slakebind.py soak 8h --no-wait
# soak 8h  ->  pull at 2025-04-01 22:37:12  (8h from now)
```

Redirected or piped, the countdown degrades to the same single line and returns
immediately, so it never stalls a log or a script:

```bash
python3 slakebind.py soak 90m >> ~/.slakebind/soaks.log
```

`examples/soak-plans.md` lists the three soaks the workshop actually runs.

## Pass 3: keep the bark ledger

Record where a batch of bark came from:

```bash
python3 slakebind.py ledger add --species oak --source "Hill Farm" --qty 25kg \
    --date 2025-04-01 --note "spring bark"
```

```
added 7ac53e18  oak  25 kg  from Hill Farm  bath date 2025-04-01  note spring bark
saved to /home/you/.slakebind/ledger.json (1 entry)
```

The id (`7ac53e18`) is what you pass to `ledger remove` later. The first add on
a fresh machine also creates `~/.slakebind` and says so.

To start from data instead of an empty ledger, copy the bundled example:

```bash
mkdir -p ~/.slakebind
cp examples/sample-ledger.json ~/.slakebind/ledger.json
```

Look at everything, newest first, with the total count:

```bash
python3 slakebind.py ledger list
```

```
ID        BATH DATE   SPECIES  SOURCE       QTY      NOTE
---------------------------------------------------------------------------------------------
b41f6c07  2025-05-02  oak      Mill Creek   8 lb     wet stand, bark lifted cleanly
1d9ab3f2  2025-04-18  hemlock  North Slope  18 kg    strong tanning bark, strip in thin sh...
7ac53e18  2025-04-01  oak      Hill Farm    25 kg    spring bark, first pull of the year
0e6f2b94  2025-03-22  birch    Ridge Stand  6.25 kg  -
c2d8075a  2025-03-14  birch    Ridge Stand  12.5 kg  peeled by hand, drying on the rack
9f31c5be  2025-02-27  oak      Wet Hollow   500 g    small trial batch for the workshop class
6 entries listed
```

Long notes are trimmed at 40 characters with `...`; nothing else is truncated.

Narrow it down - species substring plus an inclusive date window:

```bash
python3 slakebind.py ledger list --species oak --from 2025-01-01 --to 2025-06-30
```

```
ID        BATH DATE   SPECIES  SOURCE      QTY    NOTE
------------------------------------------------------------------------------------------
b41f6c07  2025-05-02  oak      Mill Creek  8 lb   wet stand, bark lifted cleanly
7ac53e18  2025-04-01  oak      Hill Farm   25 kg  spring bark, first pull of the year
9f31c5be  2025-02-27  oak      Wet Hollow  500 g  small trial batch for the workshop class
3 entries listed (species contains "oak", from 2025-01-01, to 2025-06-30)
```

Ask what is actually on hand:

```bash
python3 slakebind.py ledger list --totals
```

```
Totals by species (kg)
birch    18.75 kg   2 entries
hemlock  18 kg      1 entry
oak      29.129 kg  3 entries
-----------------------------
all      65.879 kg  6 entries
```

Mixed units are normalised for you: the 8 lb of Mill Creek oak is counted as
3.629 kg. `--unit imperial` prints the same block in pounds.

Ask for something that is not there - the command still exits 0 and tells you
what it filtered on, so a script can carry on:

```bash
python3 slakebind.py ledger list --species elm
# no entries match (species contains "elm")
# there are 6 entries in the ledger; run slakebind ledger list to see them all
```

Remove the batch you finished:

```bash
python3 slakebind.py ledger remove 7ac53e18
```

```
Entry to remove
  id:       7ac53e18
  bath:     2025-04-01
  species:  oak
  source:   Hill Farm
  qty:      25 kg
  note:     spring bark, first pull of the year
remove this entry? [y/N] y
removed 7ac53e18  oak  25 kg  bath date 2025-04-01
```

Answer `n` (or just Enter) and you get `nothing removed` with exit 0 - the file
is not touched. In a pipe there is no terminal to ask on, so the command
refuses and exits non-zero:

```bash
echo y | python3 slakebind.py ledger remove 7ac53e18
# slakebind: removing an entry needs a terminal for the confirmation, or --yes (input is not a terminal here)
python3 slakebind.py ledger remove 7ac53e18 --yes    # the non-interactive way
```

### When the ledger file goes bad

If `~/.slakebind/ledger.json` stops being valid JSON, every ledger command
refuses to touch it:

```bash
python3 slakebind.py ledger list
# slakebind: the ledger at /home/you/.slakebind/ledger.json is not valid JSON
# (Expecting value: line 1 column 1 (char 0)); nothing was changed - re-run with --reset to replace it
```

The file is left exactly as it was, so you can open it and salvage entries. When
you are ready to start over:

```bash
python3 slakebind.py ledger list --reset
# reset /home/you/.slakebind/ledger.json: 1024 bytes of ledger replaced with an empty ledger
# no entries yet in /home/you/.slakebind/ledger.json
# start one with: slakebind ledger add --species oak --source "Hill Farm" --qty 25kg
```
