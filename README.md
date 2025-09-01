# Rolbal Unified App (Streamlit)

## Run
```
pip install -r requirements.txt
streamlit run app.py
```

## Features
- Players registry, schedule gen (Swiss / Round-robin, no-repeat), rink rotation
- Mirror score entry (B mirrors A), round locks & audit log
- Rules/tiebreakers (win/draw/loss points, optional bonus on big win)
- Standings per section & combined, live leaderboard view
- Import players from Excel (Punte Sek 1/2) and export workbook
- JSON persistence in `./data/event.json`

This app combines the separate mini-apps into one UI while keeping the data format compatible.
