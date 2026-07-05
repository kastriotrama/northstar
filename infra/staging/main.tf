terraform {
  required_version = ">= 1.8.0"
}

locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

module "postgres" {
  source = "./modules/postgres"

  name_prefix = local.name_prefix
  network     = var.network
  settings    = var.postgres
  tags        = local.tags
}

module "neo4j" {
  source = "./modules/neo4j"

  name_prefix = local.name_prefix
  network     = var.network
  settings    = var.neo4j
  tags        = local.tags
}

module "elasticsearch" {
  source = "./modules/elasticsearch"

  name_prefix = local.name_prefix
  network     = var.network
  settings    = var.elasticsearch
  tags        = local.tags
}

module "redis" {
  source = "./modules/redis"

  name_prefix = local.name_prefix
  network     = var.network
  settings    = var.redis
  tags        = local.tags
}
