"""
team_aliases.py — reconcile the spreadsheet's team names with the data feed's.

Name mismatches are the quietest way to wreck a model comparison. An unmatched
team doesn't error: the grade lookup just returns nothing and the engine falls
back to Elo, so a third of the games get silently scored by a different model
than the one under test. That is exactly what happened on the first run here --
189 of 630 games (30%) were secretly Elo.

Mapping is applied AT IMPORT, so the database holds one canonical spelling and
every later query is clean.

Left side = whatever the sheet writes. Right side = the CFBD/nflverse name.
"""

CFB_ALIASES = {
    "FAU": "Florida Atlantic",
    "FIU": "Florida International",
    "Hawaii": "Hawai'i",
    "Jax State": "Jacksonville State",
    "Kennasaw State": "Kennesaw State",        # misspelled in the sheet
    "Louisiana Monroe": "UL Monroe",
    "Massachusetts (UMass)": "Massachusetts",
    "UMass": "Massachusetts",                  # duplicate row in the sheet
    "San Jose State": "San José State",
    "USF": "South Florida",
    "UTEP (Texas-El Paso)": "UTEP",
    "Uconn": "UConn",
}

ALIASES = {"cfb": CFB_ALIASES, "nfl": {}, "nba": {}}


def canonical(sport, name):
    if not name:
        return name
    name = name.strip()
    return ALIASES.get(sport, {}).get(name, name)


def unmatched(sport, sheet_names, feed_names):
    """Sheet names that still don't resolve to a feed name. Should be empty."""
    return sorted({n for n in sheet_names if canonical(sport, n) not in feed_names})
