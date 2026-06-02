FROM python:3.11-slim

WORKDIR /app

# Install Omni-Auditor engine dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install GitHub App dependencies
COPY github-app/requirements.txt ./github-app/
RUN pip install --no-cache-dir -r github-app/requirements.txt

# Copy full project (src/, github-app/, tests/, etc.)
COPY . .

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "github-app.main:app", "--host", "0.0.0.0", "--port", "8000"]
