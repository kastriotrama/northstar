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
  description = "PostgreSQL sizing and secret references."
  type = object({
    instance_class       = string
    allocated_storage_gb = number
    database_name        = string
    username_secret_name = string
    password_secret_name = string
  })
}

variable "tags" {
  description = "Common resource tags."
  type        = map(string)
}
