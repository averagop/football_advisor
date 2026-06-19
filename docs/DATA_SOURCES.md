# Data Sources

## Raw Local Files

The following non-CSV source files are stored locally under `data/raw/`.
That directory is intentionally ignored by Git because these files are large raw datasets.

| File | Source path | Purpose |
| --- | --- | --- |
| `database.sqlite` | `C:\Users\admin\Desktop\database.sqlite` | European football matches, teams, players, attributes, and bookmaker 1X2 odds. Highest-priority source for DuckDB import. |
| `games.json` | `C:\Users\admin\Desktop\games.json` | Transfermarkt-style JSONL match records with events, referees, stadiums, and attendance. Candidate for staging before core import. |
| `players_merged.json` | `C:\Users\admin\Desktop\players_merged.json` | Transfermarkt-style JSONL player profiles and transfer history. Candidate for player/entity staging. |
| `UCL_Eleme_Turlar_Verisi.xlsx` | `C:\Users\admin\Desktop\UCL_Eleme_Turlar_Verisi.xlsx` | Champions League knockout-stage history. Candidate for low-priority schedule/result staging. |
| `README.md` | `C:\Users\admin\Desktop\README.md` | Source description for the Kaggle-style match prediction dataset. |

## Import Priority

1. `database.sqlite`: deterministic import into DuckDB `core` tables.
2. `games.json`: staging only until date parsing and Transfermarkt entity mapping are explicit.
3. `players_merged.json`: staging only until player/team entity mapping is explicit.
4. `UCL_Eleme_Turlar_Verisi.xlsx`: staging or supplemental historical schedule data.
