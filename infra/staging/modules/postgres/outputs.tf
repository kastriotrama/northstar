output "blueprint" {
  description = "PostgreSQL staging service blueprint."
  value = {
    service = "postgres"
    name    = "${var.name_prefix}-postgres"
    network = var.network
    settings = {
      instance_class       = var.settings.instance_class
      allocated_storage_gb = var.settings.allocated_storage_gb
      database_name        = var.settings.database_name
      username_secret_name = var.settings.username_secret_name
      password_secret_name = var.settings.password_secret_name
    }
    tags = var.tags
  }
  sensitive = true
}
