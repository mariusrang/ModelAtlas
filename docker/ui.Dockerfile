# ModelAtlas UI image (Streamlit). Small: it only needs the UI dependencies.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv
WORKDIR /srv

COPY requirements-ui.txt .
RUN pip install --no-cache-dir -r requirements-ui.txt \
    && useradd --create-home --uid 10001 atlas

COPY ui ./ui
COPY .streamlit ./.streamlit

USER atlas
EXPOSE 8501

CMD ["streamlit", "run", "ui/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
