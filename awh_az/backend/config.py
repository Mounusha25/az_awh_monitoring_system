from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    # FastAPI Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_reload: bool = False
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
    ]
    cors_extra_origins: str = ""  # comma-separated production origins
    
    # Redis Configuration
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    cache_ttl: int = 300
    
    # Firebase Configuration
    firebase_credentials_path: str = os.path.join(
        os.path.dirname(__file__), "serviceAccountKey.json"
    )
    firestore_collection: str = "stations"
    
    # Data Configuration
    max_query_limit: int = 10000
    default_page_size: int = 100
    
    class Config:
        env_file = ".env"
        case_sensitive = False

    @property
    def all_cors_origins(self) -> List[str]:
        origins = list(self.cors_origins)
        if self.cors_extra_origins:
            origins.extend(o.strip() for o in self.cors_extra_origins.split(",") if o.strip())
        return origins


settings = Settings()
