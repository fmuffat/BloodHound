FROM python:3.12-slim

WORKDIR /app

# Install Chromium and ChromeDriver from Debian repos
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV CHROME_BIN=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

