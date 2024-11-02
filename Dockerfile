FROM python:3.9-slim-buster

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir --trusted-host=pypi.python.org --trusted-host=pypi.org --trusted-host=files.pythonhosted.org --upgrade -r requirements.txt

COPY . .

# Run the bot
CMD ["python", "main.py"]