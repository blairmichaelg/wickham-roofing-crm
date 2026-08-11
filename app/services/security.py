from pathlib import Path

from fastapi import HTTPException


def sanitize_download_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal attacks.
    Must reject if '..' is in the original input, and must strip directories.
    Must reject if the resulting filename starts with '.'.
    """
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename: path traversal detected.")
    
    clean_name = Path(filename).name
    
    if clean_name.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename: hidden files are not permitted.")
        
    return clean_name
