# Football Betting Advisor

This project is a decision-support backend for football betting analysis. It keeps
structured match data in DuckDB, keeps unstructured news in ChromaDB, computes
probabilities with an explainable model, compares those probabilities with odds,
and lets an LLM write the final report from supplied evidence only.

## Project Memory

Persistent project context is stored in:

- `AGENTS.md`
- `docs/PROJECT_REQUIREMENTS.md`
- `docs/ARCHITECTURE.md`
- `docs/PROGRESS.md`

Future Codex sessions in this workspace should read those files first and update
`docs/PROGRESS.md` after meaningful implementation changes.

## Current Implementation

- `football_advisor/probability_engine.py`: Poisson score grid plus Elo/form blending.
- `football_advisor/odds_value_engine.py`: decimal odds to normalized implied probability and edge.
- `football_advisor/no_bet_policy.py`: data freshness, risk, and No Bet gating.
- `football_advisor/backtest_engine.py`: chronological backtest with future-leakage guard.
- `football_advisor/calibration_engine.py`: Brier Score, LogLoss, accuracy.
- `football_advisor/query_tools.py`: DuckDB SELECT-only query tool with forced limits.
- `football_advisor/news_tools.py`: ChromaDB semantic news search boundary.
- `football_advisor/api.py`: FastAPI `/predict` and `/health` app factory.

## Run

Install runtime dependencies first:

```powershell
pip install -r requirements.txt
```

Start the API:

```powershell
uvicorn football_advisor.api:app --host 127.0.0.1 --port 8000
```

OpenWebUI should call `POST http://127.0.0.1:8000/predict` with:

```json
{
  "query": "Arsenal vs Chelsea",
  "mode": "standard",
  "market": "1x2"
}
```

## LLM Configuration

Set these environment variables for the external OpenAI-compatible model:

```powershell
$env:FOOTBALL_EXTERNAL_LLM_BASE_URL="https://api.example.com/v1"
$env:FOOTBALL_EXTERNAL_LLM_API_KEY="..."
$env:FOOTBALL_EXTERNAL_LLM_MODEL="..."
```

If the external API fails, the router attempts the local Ollama-compatible
backup model at `http://localhost:11434/v1` using `qwen2.5:7b`.

## Safety Positioning

The system is for pre-match decision support. It does not place bets, does not
promise profit, and should recommend `no_bet` whenever value, freshness, or risk
rules are not satisfied.
