FROM python:3.9-slim-buster

WORKDIR /app

# Update apt and install ffmpeg and SSL libraries
RUN apt-get update && \
    apt-get install -y openssl ca-certificates && \
    apt-get clean

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Run the bot
CMD ["python", "main.py"]