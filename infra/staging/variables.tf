variable "project_name" {
  description = "Project identifier used in resource names and tags."
  type        = string
  default     = "northstar"
}

variable "environment" {
  description = "Deployment environment name."
  type        = string
  default     = "staging"
}

variable "region" {
  description = "Cloud provider region for staging resources."
  type        = string
  default     = "eu-north-1"
}

variable "network" {
  description = "Networking inputs for private staging services."
  type = object({
    vpc_id              = string
    private_subnet_ids  = list(string)
    allowed_cidr_blocks = list(string)
  })
}

variable "postgres" {
  description = "PostgreSQL sizing and secret references."
  type = object({
    instance_class       = string
    allocated_storage_gb = number
    database_name        = string
    username_secret_name = string
    password_secret_name = string
  })
}

variable "neo4j" {
  description = "Neo4j sizing and secret references."
  type = object({
    instance_type        = string
    volume_size_gb       = number
    username_secret_name = string
    password_secret_name = string
  })
}

variable "elasticsearch" {
  description = "Elasticsearch/OpenSearch sizing."
  type = object({
    instance_type  = string
    instance_count = number
    volume_size_gb = number
  })
}

variable "redis" {
  description = "Redis sizing."
  type = object({
    node_type        = string
    replica_count    = number
    auth_secret_name = string
  })
}
