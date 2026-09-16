from pathlib import Path

from services.api.omni_api.config import Settings


class AttachmentStorage:
    def __init__(self, settings: Settings):
        self.settings = settings

    def put(self, object_key: str, content: bytes, content_type: str) -> None:
        if self.settings.storage_backend == "s3":
            client = self._s3_client()
            self._ensure_bucket(client)
            options = dict(
                Bucket=self.settings.s3_bucket,
                Key=object_key,
                Body=content,
                ContentType=content_type,
            )
            if self.settings.s3_server_side_encryption:
                options["ServerSideEncryption"] = self.settings.s3_server_side_encryption
            client.put_object(**options)
            return
        destination = Path(self.settings.attachment_dir) / object_key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    def get(self, object_key: str) -> bytes:
        if self.settings.storage_backend == "s3":
            response = self._s3_client().get_object(Bucket=self.settings.s3_bucket, Key=object_key)
            return response["Body"].read()
        return (Path(self.settings.attachment_dir) / object_key).read_bytes()

    def delete(self, object_key: str) -> None:
        if self.settings.storage_backend == "s3":
            self._s3_client().delete_object(Bucket=self.settings.s3_bucket, Key=object_key)
            return
        path = Path(self.settings.attachment_dir) / object_key
        path.unlink(missing_ok=True)

    def _s3_client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            region_name="us-east-1",
        )

    def _ensure_bucket(self, client) -> None:
        try:
            client.head_bucket(Bucket=self.settings.s3_bucket)
        except Exception:
            client.create_bucket(Bucket=self.settings.s3_bucket)
