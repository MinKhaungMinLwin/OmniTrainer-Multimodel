output "database_endpoint" { value = aws_db_instance.postgres.address }
output "redis_endpoint" { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "call_data_bucket" { value = aws_s3_bucket.call_data.id }
output "orchestration_queue_url" { value = aws_sqs_queue.orchestration.url }
output "container_repositories" { value = { for key, repo in aws_ecr_repository.services : key => repo.repository_url } }
output "kubernetes_cluster" { value = aws_eks_cluster.omni.name }
