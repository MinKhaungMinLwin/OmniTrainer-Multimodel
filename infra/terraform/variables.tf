variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production"
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.42.0.0/16"
}

variable "database_username" {
  type      = string
  default   = "omni"
  sensitive = true
}

variable "container_image_tag" {
  type    = string
  default = "latest"
}
