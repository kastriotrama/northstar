output "blueprint" {
  description = "Elasticsearch staging service blueprint."
  value = {
    service = "elasticsearch"
    name    = "${var.name_prefix}-elasticsearch"
    network = var.network
    settings = {
      instance_type  = var.settings.instance_type
      instance_count = var.settings.instance_count
      volume_size_gb = var.settings.volume_size_gb
    }
    tags = var.tags
  }
}
