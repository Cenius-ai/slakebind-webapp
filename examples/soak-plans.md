# Soak plans from the workshop bench

Hand-check the ratio first, then start the soak. A bare number is minutes.

| Soak | Command | Why |
| --- | --- | --- |
| Short test | `python3 slakebind.py soak 45s` | proves the alert works before you commit the hide |
| Standard lime bath | `python3 slakebind.py soak 1h30m` | the everyday soak for a lime bath freshen-up |
| Overnight | `python3 slakebind.py soak 8h` | leave the terminal open, or note the pull time and walk away |

If you are going to be away from the terminal:

```bash
python3 slakebind.py soak 8h --no-wait
```

prints the wall-clock pull time and returns immediately. Writing the same
command into a log file gives you the same single line with no carriage returns:

```bash
python3 slakebind.py soak 8h >> ~/.slakebind/soaks.log
```

While a soak runs in a terminal, `Ctrl-C` cancels it and reports how much of
the soak was left, so you can write down where you stopped.
