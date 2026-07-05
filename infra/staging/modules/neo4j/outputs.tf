output "blueprint" {
  description = "Neo4j staging service blueprint."
  value = {
    service = "neo4j"
    name    = "${var.name_prefix}-neo4j"
    network = var.network
    settings = {
      instance_type        = var.settings.instance_type
      volume_size_gb       = var.settings.volume_size_gb
      username_secret_name = var.settings.username_secret_name
      password_secret_name = var.settings.password_secret_name
    }
    tags = var.tags
  }
  sensitive = true
}
