variable "name_prefix" {
  description = "Common resource name prefix."
  type        = string
}

variable "network" {
  description = "Networking inputs for the service."
  type = object({
    vpc_id              = string
    private_subnet_ids  = list(string)
    allowed_cidr_blocks = list(string)
  })
}

variable "settings" {
  description = "Elasticsearch/OpenSearch sizing."
  type = object({
    instance_type  = string
    instance_count = number
    volume_size_gb = number
  })
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
}
