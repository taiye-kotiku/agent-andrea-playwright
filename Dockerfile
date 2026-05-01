FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV WEGEST_USERNAME=${WEGEST_USERNAME}
ENV WEGEST_PASSWORD=${WEGEST_PASSWORD}
ENV API_SECRET=${API_SECRET}

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
