# Mac Docker + CloudShell password (no local AWS login)

Use this when Windows/Mac **cannot** run `aws login` / access keys, but you **can** open the AWS Console and **CloudShell**, and you have **Docker** on a Mac.

**Region:** `eu-north-1` (Stockholm)  
**LLM now:** Gemini API (optional key in Secrets Manager or UI)  
**Not yet:** Bedrock

```text
CloudShell (browser, already logged into AWS)
  └─ phase1: create stack (DesiredCount=0) + print ECR password
Mac (Docker Desktop only — no aws configure)
  └─ docker login with pasted password → build → push to ECR
CloudShell
  └─ phase3: scale ECS=1, sync UI to S3, invalidate CloudFront
```

ECR passwords expire in ~**12 hours**. Re-run phase1 (or just `aws ecr get-login-password`) if login fails.

---

## 0) Prerequisites

| Where | Need |
|-------|------|
| AWS Console | Sign in as `App@Team` (account `912564796433`) |
| CloudShell | Open in region **eu-north-1**; IAM can create CFN/ECS/ECR/S3/CloudFront/… |
| Mac | Docker Desktop running; this git repo cloned |
| GitHub | CloudShell can `git clone` this repo (public or with a token) |

---

## 1) CloudShell — create infra + get password

In the console top-right, set region to **Stockholm (eu-north-1)**, open **CloudShell**:

```bash
git clone https://github.com/AkshaySriKiran/IDP-python-env.git
cd IDP-python-env
# optional: git checkout cursor/aws-eu-north1-preflight-4681   # if scripts not on main yet
chmod +x infra/cloudshell-phase1.sh infra/cloudshell-phase3.sh
./infra/cloudshell-phase1.sh
```

Optional Gemini default:

```bash
export GEMINI_API_KEY="your_key"
./infra/cloudshell-phase1.sh
```

Copy from the output:

1. `ECR_URI=...`
2. The **ECR password** block
3. Keep the CloudShell tab open for phase3

---

## 2) Mac — login, build, push

```bash
cd /path/to/IDP-python-env
chmod +x infra/mac-push-ecr.sh

export AWS_REGION=eu-north-1
export ECR_URI='...paste from CloudShell...'
export IMAGE_TAG="$(git rev-parse --short HEAD)"
export ECR_PASSWORD='...paste password from CloudShell...'

./infra/mac-push-ecr.sh
```

Or without the helper:

```bash
echo 'PASTE_PASSWORD' | docker login --username AWS --password-stdin \
  912564796433.dkr.ecr.eu-north-1.amazonaws.com

docker build -t omniparse-idp-api:$IMAGE_TAG -f backend/Dockerfile backend
docker tag omniparse-idp-api:$IMAGE_TAG $ECR_URI:$IMAGE_TAG
docker tag omniparse-idp-api:$IMAGE_TAG $ECR_URI:latest
docker push $ECR_URI:$IMAGE_TAG
docker push $ECR_URI:latest
```

---

## 3) CloudShell — finish deploy

Use the **same** `IMAGE_TAG` you pushed:

```bash
export IMAGE_TAG=abc1234   # same as Mac
# export GEMINI_API_KEY=...  # optional if not set in phase1
./infra/cloudshell-phase3.sh
```

Open the printed **CloudFront URL** → header should show **Python API Ready** → try a small PDF.

Logs: CloudWatch log group `/ecs/omniparse-idp` in `eu-north-1`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Cannot perform an interactive login from a non TTY device` | Use `echo 'password' \| docker login ...` or `ECR_PASSWORD=... ./infra/mac-push-ecr.sh` |
| `no basic auth credentials` on push | Password expired or wrong registry; re-run phase1 password |
| ECS tasks crash / API not ready | Image tag mismatch; set `IMAGE_TAG` in phase3 to the pushed tag |
| CloudShell wrong AZ/region | Recreate resources in **eu-north-1** only |
| Clone fails (private repo) | Upload zip to CloudShell, or clone with a GitHub PAT |

---

## vs full `./infra/deploy.sh`

| | `deploy.sh` | Mac + CloudShell |
|--|-------------|------------------|
| Needs local AWS CLI login | Yes | No |
| Docker where? | Same machine as AWS CLI | Mac only |
| AWS API calls | Local | CloudShell (browser session) |
| Same end state | Yes | Yes |
