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
  description = "Redis sizing and secret references."
  type = object({
    node_type        = string
    replica_count    = number
    auth_secret_name = string
  })
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
}
