# Deployment Guide

## Production Checklist

1. Copy `.env.example` to `.env` and fill all secrets.
2. Create database schema:
   - `psql < database/schema.sql`
3. Install dependencies:
   - `pip install -r requirements.txt`
4. Run migrations or metadata bootstrap if needed:
   - `python scripts/seed_database.py`
5. Start API service:
   - `uvicorn api.main:app --host 0.0.0.0 --port 8000`
6. Start ingestion workers:
   - `python -m data_ingestion.main`
7. Start analytics workers:
   - `python -m processing.sentiment.main`
   - `python -m signals.main`
8. Start dashboard:
   - `streamlit run dashboard/app.py`
9. Optional terminal dashboard:
   - `python -m terminal.cli status`

## Docker Notes

- Ensure TimescaleDB extension is enabled in Postgres image.
- Mount model cache volumes for Prophet/LSTM runtime dependencies.
- Restrict outbound network from API container except required data providers.

## Monitoring

- Collect application logs in JSON format.
- Add health checks for API `/health` and websocket endpoint `/ws/market`.
- Alert on ingestion lag, signal generation failures, and model prediction drift.
