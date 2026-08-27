# AgentCore Runtime requires linux/arm64 images.
# Build with: docker buildx build --platform linux/arm64 -t <tag> --load .
FROM --platform=linux/arm64 python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py config.py ./

# BedrockAgentCoreApp.run() serves /invocations (POST) and /ping (GET) on 8080.
EXPOSE 8080

CMD ["python", "main.py"]
