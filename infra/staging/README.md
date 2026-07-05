# Staging Infrastructure

Terraform skeleton for staging datastore infrastructure.

## Scope

- PostgreSQL
- Neo4j
- Elasticsearch
- Redis

Each datastore has a provider-neutral Terraform module boundary under
`infra/staging/modules`. The modules currently emit validated service
blueprints instead of creating cloud resources. This keeps the skeleton
plan-safe until the staging provider, network, cost, backup, and access choices
are confirmed.

## Usage

```sh
cd infra/staging
terraform init
terraform fmt -check
terraform validate
```

Create a local variable file from the example before planning:

```sh
cp terraform.tfvars.example terraform.tfvars
terraform plan
```

Do not commit `terraform.tfvars`, state files, plans, or real secret names that expose production details.
