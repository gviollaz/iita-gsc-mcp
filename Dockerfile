FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# server.py, not main.py: main.py defines the tools but has no authentication.
CMD ["python", "server.py"]
