# APRS symbols

64×64 px sprite sheets from the aprs.fi symbol set by Heikki Hannikainen, OH7LZB:
https://github.com/hessu/aprs-symbols (commit f2286a9). See COPYRIGHT.md for per-symbol sources and licenses.

- `aprs-symbols-64-0.png`: primary table (`/`)
- `aprs-symbols-64-1.png`: alternate table (`\`)
- `aprs-symbols-64-2.png`: overlay characters, drawn on an alternate symbol when the table character is 0-9 or A-Z

Each sheet is 16 columns × 6 rows. Symbol character `c` is at index `ord(c) - 33`: column `index % 16`, row `index // 16`.
