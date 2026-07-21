import bcrypt


def hash_password(password: str) -> str:
    clean = password.strip()
    return bcrypt.hashpw(clean.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        clean_hash = password_hash.strip()
        if clean_hash.startswith("'"):
            clean_hash = clean_hash[1:]
        return bcrypt.checkpw(plain_password.encode("utf-8"), clean_hash.encode("utf-8"))
    except Exception:
        return False
