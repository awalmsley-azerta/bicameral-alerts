# bicameral-alertas: the shared alerts service (python -m servicio).
# The old SQS prototype (main.py) is no longer built; its running task keeps its
# last image until it is retired.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements-servicio.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements-servicio.txt

COPY alertas ./alertas
COPY servicio ./servicio

USER nobody
ENTRYPOINT ["python", "-m", "servicio"]
CMD ["servir", "--simular"]
