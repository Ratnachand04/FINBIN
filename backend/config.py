from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "BINFIN"
    env: str = "development"
    log_level: str = "INFO"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "binfin"
    postgres_password: str = "binfin"
    postgres_db: str = "binfin"
    redis_host: str = "localhost"
    redis_port: int = 6379

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
