# Batch worker image for Azure Container Apps Jobs (simulate / analyse / build-store).
# Built in the cloud with `az acr build` -- you do not need Docker installed locally.
# Contains the simulator (private side); keep the registry private.
FROM python:3.12-slim
WORKDIR /app
COPY deploy/worker/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY team_player ./team_player
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "team_player.cloud.worker"]
