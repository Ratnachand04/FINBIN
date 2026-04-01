import redis

from backend.config import settings


def get_redis_client() -> redis.Redis:
    return redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
