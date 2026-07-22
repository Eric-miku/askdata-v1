FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN addgroup --system askdata && adduser --system --ingroup askdata askdata
COPY pyproject.toml README.md ./
COPY backend ./backend
RUN pip install --no-cache-dir .

USER askdata
EXPOSE 8000
CMD ["uvicorn", "askdata.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
