from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    # FastAPI Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = True
    cors_origins: List[str] = ["http://localhost:3000", "http://localhost:3001"]
    
    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    cache_ttl: int = 300
    
    # Google Cloud Configuration
    google_application_credentials: str = ""
    gcs_bucket_name: str = "awh-station-data"
    firestore_collection: str = "station_readings"
    
    # Data Configuration
    max_query_limit: int = 10000
    default_page_size: int = 100
    
    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
