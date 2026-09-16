data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "omni" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "omni-${var.environment}" }
}

resource "aws_internet_gateway" "omni" {
  vpc_id = aws_vpc.omni.id
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.omni.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "omni-${var.environment}-public-${count.index + 1}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.omni.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "omni-${var.environment}-private-${count.index + 1}" }
}

resource "aws_eip" "nat" {
  domain     = "vpc"
  depends_on = [aws_internet_gateway.omni]
}

resource "aws_nat_gateway" "omni" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.omni.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.omni.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.omni.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.omni.id
  }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "data" {
  name   = "omni-${var.environment}-data"
  vpc_id = aws_vpc.omni.id
  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "omni" {
  name       = "omni-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier                  = "omni-${var.environment}"
  engine                      = "postgres"
  engine_version              = "17"
  instance_class              = var.environment == "production" ? "db.r7g.large" : "db.t4g.micro"
  allocated_storage           = var.environment == "production" ? 100 : 20
  max_allocated_storage       = 500
  storage_encrypted           = true
  username                    = var.database_username
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.omni.name
  vpc_security_group_ids      = [aws_security_group.data.id]
  multi_az                    = var.environment == "production"
  backup_retention_period     = var.environment == "production" ? 30 : 7
  deletion_protection         = var.environment == "production"
  skip_final_snapshot         = false
  final_snapshot_identifier   = "omni-${var.environment}-final"
}

resource "aws_elasticache_subnet_group" "omni" {
  name       = "omni-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id       = "omni-${var.environment}"
  description                = "Omni durable orchestration cache"
  node_type                  = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.micro"
  num_cache_clusters         = var.environment == "production" ? 2 : 1
  automatic_failover_enabled = var.environment == "production"
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  subnet_group_name          = aws_elasticache_subnet_group.omni.name
  security_group_ids         = [aws_security_group.data.id]
}

resource "aws_s3_bucket" "call_data" {
  bucket_prefix = "omni-${var.environment}-call-data-"
}

resource "aws_s3_bucket_versioning" "call_data" {
  bucket = aws_s3_bucket.call_data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "call_data" {
  bucket = aws_s3_bucket.call_data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "call_data" {
  bucket = aws_s3_bucket.call_data.id
  rule {
    id     = "archive-call-data"
    status = "Enabled"
    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }
    transition {
      days          = 90
      storage_class = "GLACIER"
    }
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_sqs_queue" "dead_letter" {
  name                      = "omni-${var.environment}-dead-letter"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "orchestration" {
  name                       = "omni-${var.environment}-orchestration"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 5
  })
}

resource "aws_ecr_repository" "services" {
  for_each             = toset(["api", "worker", "voice"])
  name                 = "omni-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = aws_ecr_repository.services
  name              = "/omni/${var.environment}/${each.key}"
  retention_in_days = var.environment == "production" ? 90 : 14
}

resource "aws_secretsmanager_secret" "application" {
  name                    = "omni/${var.environment}/application"
  recovery_window_in_days = var.environment == "production" ? 30 : 7
}
