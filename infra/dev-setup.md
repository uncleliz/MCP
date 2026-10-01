# Dev/test infra setup (T-015)

> Deviation from `implementation-plan.md`: the plan lists this file as
> `docs/dev-setup.md`. `squad-backend`'s write scope for this dispatch is limited to
> `packages/`, `infra/`, `scripts/`, `ci/`, `docs/spikes/`, `docs/signoff/`, `eval/` —
> it excludes other paths directly under `docs/`. This file is placed at
> `infra/dev-setup.md` instead; content and purpose are unchanged. Flag for Lead/SA
> to relocate if the docs/ restriction was unintentional.

## What this brings up

`infra/docker-compose.yml` starts the 4 sources that can be emulated locally
(architecture.md "Hạ tầng dev/test"). The other 5 sources (Confluence, GitLab,
OpenSearch, Kibana, CloudWatch) are real remote systems — see
`docs/spikes/S1-reachability.md` and `scripts/probe_reachability.py` — and are never
emulated; their tests run against `respx`/mocked fixtures instead.

| Service | Image | Port | Purpose |
|---|---|---|---|
| `postgres` | `pgvector/pgvector:pg16` | 5432 | `kb` schema for `mcp-pgvector` + `mcp-ingest` (Phase 3) |
| `redis` | `redis:7` | 6379 | `mcp-redis` dev/test target, ACL user `mcp_ro` |
| `kafka` | `apache/kafka:3.7.0` (KRaft, single node) | 9092 | `mcp-kafka` dev/test target |
| `localstack` | `localstack/localstack:3` (`sqs,sns`) | 4566 | `mcp-sqs-sns` dev/test target |

## Usage

```bash
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml ps
docker compose -f infra/docker-compose.yml down -v   # -v also drops the postgres volume
```

## Verifying each service

**Postgres + pgvector** (schema `kb`, Phase 3)

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U mcp_admin -d mcp_kb -c "SELECT extname FROM pg_extension;"

# Apply the numbered migrations 0001-0006 (idempotent; re-running applies nothing):
MCP_INGEST_ADMIN_DSN=postgresql://mcp_admin:mcp_admin_dev_password@localhost:5432/mcp_kb \
  uv run mcp-ingest db upgrade            # add --dry-run / --json as needed

# 0005_roles.sql creates the roles WITHOUT passwords (never commit one). Set them once, out of
# band, then put the DSNs in your environment (see .env.example):
docker compose -f infra/docker-compose.yml exec postgres psql -U mcp_admin -d mcp_kb \
  -c "ALTER ROLE mcp_query_ro PASSWORD 'choose-a-dev-password'" \
  -c "ALTER ROLE mcp_ingest_rw PASSWORD 'choose-another-dev-password'"

uv run mcp-pgvector doctor                # MCP_PGVECTOR_DSN must be the mcp_query_ro DSN
```

`mcp-pgvector` refuses to serve with the `mcp_ingest_rw` (or a superuser) DSN, and when the
configured embedding model differs from the one stored in `kb.chunks`.

*Without Docker:* the Postgres/pgvector tests start a throw-away cluster themselves
(`initdb`/`pg_ctl` from the distro packages, `apt install postgresql postgresql-16-pgvector`; see
`packages/conftest.py`). The distro pgvector may be older than 0.8 (no `hnsw.iterative_scan`); the
server then uses the documented over-fetch fallback and says so in `doctor`. SQS/SNS tests use an
in-process AWS emulator (moto) instead of LocalStack.

**Redis — `mcp_ro` ACL user (ADR-0008 A1)**

`infra/redis/users.acl` has no comments in it — Redis's `aclfile` loader does not
support `#` comment lines (unlike `redis.conf`); every non-blank line must literally
start with the `user` keyword, so the rationale for `mcp_ro`'s command list and the
`+acl|getuser` grant lives here instead:

- `-@all` denies every command by default, then a fixed read-command allowlist is
  granted back (ADR-0008's Redis command allowlist v1).
- `+acl|whoami` and `+acl|getuser` are both required for the startup credential
  self-check (ADR-0008 A1): `ACL WHOAMI` alone only returns the username, not the
  user's actual permissions, so the gate cannot verify read-only-ness without
  `ACL GETUSER` access too.
- The password is a throwaway dev-only value, never used against a real Redis instance.

```bash
docker compose -f infra/docker-compose.yml exec redis \
  redis-cli -a mcp_admin_dev_password --no-auth-warning \
  --user mcp_ro --pass mcp_ro_dev_password ACL WHOAMI
docker compose -f infra/docker-compose.yml exec redis \
  redis-cli -a mcp_admin_dev_password --no-auth-warning \
  --user mcp_ro --pass mcp_ro_dev_password ACL GETUSER mcp_ro
# GETUSER must show no write category granted — this is exactly what
# mcp_common.runtime's startup credential gate checks (ADR-0003 A1).
```

**Kafka — auto-create disabled (R17 / ADR-0009 A1)**

```bash
docker compose -f infra/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

**LocalStack — seeded queue + topic**

```bash
aws --endpoint-url=http://localhost:4566 --region ap-southeast-1 sqs list-queues
aws --endpoint-url=http://localhost:4566 --region ap-southeast-1 sns list-topics
```

## Notes

- `MCP_REDACT_DISABLED`, `MCP_ALLOW_UNVERIFIED_CREDENTIALS`, and every other shared
  variable are documented in `.env.example` at the repo root, not here — this file is
  only about the 4 emulated services above.
- Passwords in `infra/redis/users.acl` and `infra/docker-compose.yml` are throwaway
  dev-only values, never used against a real system.
