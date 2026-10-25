"""Exit 0 if this container's worker wrote a heartbeat in the last 30 seconds."""

import sys

import redis

from agenttrace_worker.main import HEARTBEAT_KEY
from agenttrace_worker.settings import Settings

s = Settings()
alive = redis.Redis.from_url(s.redis_url).exists(HEARTBEAT_KEY.format(consumer=s.consumer_name))
sys.exit(0 if alive else 1)
