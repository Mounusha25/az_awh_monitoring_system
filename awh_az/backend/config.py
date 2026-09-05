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
        "http://localhost:3002",
        "http://localhost:3003",
        "https://azawhdashboard.vercel.app",
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
        os.path.dirname(__file__), "awh-project-460421-52cd6ebf2aa3.json"
    )
    firestore_collection: str = "stations"

    # PostgreSQL Configuration — serves /readings and /hourly (kept in sync
    # by ingestion_worker.py); Firestore remains the source of truth for
    # /stations, /stations-registry, and /impact.
    database_url: str = "postgresql://mounusha@localhost:5432/awh_db"

    # Admin panel — shared secret the dashboard's server-side admin API routes
    # attach to admin writes; never sent to the browser. Firebase Storage bucket
    # backs station image uploads. Both empty by default (fail closed).
    admin_api_key: str = ""
    firebase_storage_bucket: str = ""

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
