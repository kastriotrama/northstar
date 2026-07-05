output "blueprint" {
  description = "Redis staging service blueprint."
  value = {
    service = "redis"
    name    = "${var.name_prefix}-redis"
    network = var.network
    settings = {
      node_type        = var.settings.node_type
      replica_count    = var.settings.replica_count
      auth_secret_name = var.settings.auth_secret_name
    }
    tags = var.tags
  }
  sensitive = true
}
