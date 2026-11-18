"""Start the existing FastAPI module with runtime-only configuration."""
import os
import sys
from config import bind_config

bind_config()
os.execv(sys.executable, [sys.executable, '-m', 'dataquery_agent.api'])
