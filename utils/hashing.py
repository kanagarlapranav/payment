import hashlib
import os

def generate_image_hash(filepath: str) -> str:
    """Generates a SHA-256 hash for an image file."""
    if not os.path.exists(filepath):
        return ""
        
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        # Read and update hash string value in blocks of 4K
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()
