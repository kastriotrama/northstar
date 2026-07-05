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
  description = "Neo4j sizing and secret references."
  type = object({
    instance_type        = string
    volume_size_gb       = number
    username_secret_name = string
    password_secret_name = string
  })
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
}
