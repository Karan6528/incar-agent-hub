# Deploying to AWS App Runner

App Runner runs a container from Amazon ECR and gives it an HTTPS URL. The whole hub (MCP server, A2A agent, host agent and UI) is one container.

> **Cost:** App Runner bills while the service is running. Pause or delete the service when you're not demoing it.

## 1. Prerequisites

- AWS account, AWS CLI v2 (`aws configure`), Docker Desktop
- Choose a region, e.g. `eu-central-1` (Frankfurt)

## 2. Build and push the image to ECR

```bash
REGION=eu-central-1
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REPO=incar-agent-hub

aws ecr create-repository --repository-name $REPO --region $REGION
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

# App Runner runs x86_64, so build for linux/amd64 (important on Apple Silicon)
docker build --platform linux/amd64 -t $REPO .
docker tag $REPO:latest $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
```

(Windows PowerShell: set variables with `$REGION="eu-central-1"` and so on. The commands are the same.)

## 3. Create the App Runner service (console)

1. App Runner → **Create service** → Source: *Container registry* → *Amazon ECR* → pick the image.
2. Deployment trigger: *Manual*. ECR access role: *Create new service role*.
3. Port: **8080**.
4. Environment variables:
   - `API_KEY`: a long random string. Better: store it in **AWS Secrets Manager** and reference it as a secret.
   - `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`: optional, see `.env.example`.
5. Health check: HTTP, path `/health`.
6. Instance size: 0.25 vCPU / 0.5 GB is enough.
7. Create. When it's running, copy the default domain, e.g. `https://abc123.eu-central-1.awsapprunner.com`.
8. Add `PUBLIC_BASE_URL=https://abc123.eu-central-1.awsapprunner.com` and redeploy, so the A2A Agent Card shows the public URL.

## 4. Check it works

```bash
URL=https://abc123.eu-central-1.awsapprunner.com
curl $URL/health
curl $URL/.well-known/agent-card.json
curl -X POST $URL/api/chat -H "X-API-Key: <your key>" -H "content-type: application/json" \
     -d '{"message": "I am cold and need to charge before Berlin"}'
```

Open `$URL/` in a browser, enter the API key at the top right and use the demo UI.

## Production notes (beyond this prototype)

- Split the services (MCP server, charging agent, host agent) with separate scaling and IAM. They already talk only over HTTP (`MCP_URL`, `A2A_AGENT_URL`).
- Replace the shared API key with OAuth 2.1 / Cognito and per-vehicle scopes.
- Use EC2 or ECS instead if you need VPC-internal backends, GPUs or long-lived connections.
