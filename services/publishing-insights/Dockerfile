FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY publishing ./publishing
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home publishing && mkdir /data && chown publishing /data
USER publishing
ENV PUBLISHING_DATABASE_PATH=/data/publishing.sqlite3
EXPOSE 8014
CMD ["python", "-m", "publishing"]
