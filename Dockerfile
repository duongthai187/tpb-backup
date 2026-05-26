FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create log directory
RUN mkdir -p logs webhook_notifications webhook_notifications_uat

EXPOSE 8443

CMD ["python", "server.py"]
