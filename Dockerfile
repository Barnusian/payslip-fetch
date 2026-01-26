FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y qpdf \
    && rm -rf /var/lib/apt/lists/*

# Ensure logs appear in Portainer immediately
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY fetch_payslips.py .

CMD ["python", "fetch_payslips.py"]
