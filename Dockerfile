FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing .pyc and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Copy all project files
COPY . /app

EXPOSE 5000

# Run the server
CMD ["python", "server.py"]
