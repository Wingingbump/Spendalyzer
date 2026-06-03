import os
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Must be set before any app modules are imported — auth.py reads JWT_SECRET_KEY at module level
os.environ["JWT_SECRET_KEY"] = "test-only-secret-key-not-for-production-use-1234"
os.environ["SECURE_COOKIES"] = "false"
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["ANTHROPIC_API_KEY"] = "test-key"  # prevents anthropic client from erroring on import

# Load .env (DATABASE_URL etc.) without overriding the test values set above, so
# core.db can connect at import regardless of the shell environment.
load_dotenv(override=False)
