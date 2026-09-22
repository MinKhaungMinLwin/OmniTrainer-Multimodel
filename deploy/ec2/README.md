# EC2 staging deployment

This deployment runs the API, worker, voice gateway, PostgreSQL, Redis, MinIO,
and Caddy on one small EC2 host. It is intended for staging and demonstrations,
not a highly available production deployment.

## Prerequisites

- Amazon Linux 2023 EC2 host with Docker and Docker Compose
- 4 GB RAM and at least 20 GB of disk
- TCP ports 80 and 443 open; SSH restricted to the operator's IP
- `api` and `voice` DNS A records pointing to the EC2 Elastic IP

## First deployment

Clone the repository and create the untracked environment file:

```bash
sudo mkdir -p /opt/omni
sudo chown ec2-user:ec2-user /opt/omni
git clone https://github.com/MinKhaungMinLwin/OmniTrainer-Multimodel.git /opt/omni
cd /opt/omni
cp deploy/ec2/staging.env.example .env.staging
chmod 600 .env.staging
```

Generate independent secrets with `openssl rand -hex 32`, edit every placeholder
in `.env.staging`, and validate the rendered configuration without printing it:

```bash
docker compose --env-file .env.staging -f compose.staging.yaml config --quiet
```

Deploy migrations and services:

```bash
./scripts/deploy_ec2_staging.sh
```

Check the public health endpoints after Caddy obtains certificates:

```bash
curl --fail --silent --show-error https://api.example.com/health/ready
curl --fail --silent --show-error https://voice.example.com/health/ready
```

## Updates and operations

```bash
cd /opt/omni
git pull --ff-only
./scripts/deploy_ec2_staging.sh
docker compose --env-file .env.staging -f compose.staging.yaml ps
docker compose --env-file .env.staging -f compose.staging.yaml logs --tail=100 api
```

Do not use `docker compose down --volumes`; that deletes the staging database,
Redis state, object storage, and TLS certificate data. Back up the named volumes
before instance replacement or destructive maintenance.

Development authentication creates shared demo identities and must only be used
while access is restricted to trusted IP addresses. Keep
`OMNI_ALLOW_DEV_AUTH=false` before making the service public or connecting a
public Vercel deployment.
