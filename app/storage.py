import os
from pathlib import Path
from tempfile import NamedTemporaryFile

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None


def configured():
    return all(os.getenv(k) for k in ("S3_ENDPOINT_URL", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET"))


def _client():
    if boto3 is None:
        raise RuntimeError("boto3 is required for S3-compatible PDF storage")
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("S3_REGION") or "auto",
    )


def upload_file(local_path: Path, key: str) -> str:
    if not configured():
        raise RuntimeError("Persistent PDF storage is not configured. Set S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY and S3_BUCKET.")
    _client().upload_file(str(local_path), os.environ["S3_BUCKET"], key, ExtraArgs={"ContentType": "application/pdf"})
    return key


def download_file(key: str, suffix=".pdf") -> Path:
    if not configured():
        raise RuntimeError("Persistent PDF storage is not configured")
    tmp = NamedTemporaryFile(prefix="voter-pdf-", suffix=suffix, delete=False)
    tmp.close()
    _client().download_file(os.environ["S3_BUCKET"], key, tmp.name)
    return Path(tmp.name)


def delete_file(key: str) -> None:
    if configured() and key:
        _client().delete_object(Bucket=os.environ["S3_BUCKET"], Key=key)
