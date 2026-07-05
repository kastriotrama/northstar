output "name_prefix" {
  description = "Common name prefix for staging resources."
  value       = local.name_prefix
}

output "environment" {
  description = "Resolved deployment environment."
  value       = var.environment
}

output "region" {
  description = "Resolved cloud provider region."
  value       = var.region
}

output "service_blueprints" {
  description = "Provider-neutral staging service blueprints."
  value = {
    postgres      = module.postgres.blueprint
    neo4j         = module.neo4j.blueprint
    elasticsearch = module.elasticsearch.blueprint
    redis         = module.redis.blueprint
  }
  sensitive = true
}
