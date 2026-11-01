FROM node:22-slim AS widget
WORKDIR /build/widget
COPY widget/package.json widget/package-lock.json ./
RUN npm ci
COPY widget/ ./
RUN mkdir -p ../src/northstar_webchat/static && npx vite build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY --from=widget /build/src/northstar_webchat/static/widget.js ./src/northstar_webchat/static/widget.js
RUN pip install --no-cache-dir .

EXPOSE 4020

CMD ["uvicorn", "--factory", "northstar_webchat.main:create_from_env", "--host", "0.0.0.0", "--port", "4020"]
