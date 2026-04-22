import httpx
from fastapi import FastAPI, HTTPException, Depends, Body
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import os

from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# Database Configuration
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/energyprices")

# Global variables for lazy initialization
_engine = None
_SessionLocal = None
Base = declarative_base()

# --- Lazy Initialization Helpers ---
def get_engine():
    global _engine
    if _engine is None:
        # Engine is created only when needed
        _engine = create_engine(DATABASE_URL)
        # Create tables if they don't exist
        Base.metadata.create_all(bind=_engine)
    return _engine

def get_session_local():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

# --- Database Models ---
class SpotPrice(Base):
    __tablename__ = "spot_prices"
    
    id = Column(Integer, primary_key=True, index=True)
    time_start = Column(DateTime(timezone=True), unique=True, index=True)
    sek_per_kwh = Column(Float, nullable=False)
    zone = Column(String, index=True)

app = FastAPI(title="Energy Cost API with PostgreSQL Cache")

# Dependency to get DB session
def get_db():
    # Use the lazy-initialized session factory
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Pydantic Schemas ---
class ApplianceTask(BaseModel):
    consumption_kwh: float
    duration_mins: int

class PriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True) # Nueva forma
    
    id: Optional[int] = None
    time_start: datetime
    sek_per_kwh: float
    zone: Optional[str] = "SE3"

    class Config:
        from_attributes = True

# --- CRUD Endpoints ---
@app.get("/prices", response_model=List[PriceResponse])
def get_all_prices(db: Session = Depends(get_db)):
    return db.query(SpotPrice).all()

@app.delete("/prices", status_code=200)
def clear_all_prices(db: Session = Depends(get_db)):
    db.query(SpotPrice).delete()
    db.commit()
    return {"message": "All cached prices deleted"}

@app.get("/prices/refresh")
async def refresh_prices(db: Session = Depends(get_db)):
    # Triggering the fetch logic via a dummy task
    await calculate_cheapest_time(ApplianceTask(consumption_kwh=0, duration_mins=0), db)
    return {"message": "Prices updated from external API"}

@app.post("/prices", response_model=PriceResponse)
def create_price(price: PriceResponse, db: Session = Depends(get_db)):
    db_price = SpotPrice(
        time_start=price.time_start,
        sek_per_kwh=price.sek_per_kwh,
        zone=price.zone or "SE3"
    )
    db.add(db_price)
    db.commit()
    db.refresh(db_price)
    return db_price

@app.delete("/prices/{price_id}")
def delete_price(price_id: int, db: Session = Depends(get_db)):
    price = db.query(SpotPrice).filter(SpotPrice.id == price_id).first()
    if not price:
        raise HTTPException(status_code=404, detail="Price not found")
    db.delete(price)
    db.commit()
    return {"message": "Price deleted"}

# --- Business Logic ---
@app.post("/calculate-cost")
async def calculate_cheapest_time(task: ApplianceTask, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    
    cached_price = db.query(SpotPrice).filter(SpotPrice.time_start == current_hour).first()
    
    if not cached_price:
        today_local = datetime.now()
        year = today_local.strftime("%Y")
        date_str = today_local.strftime("%m-%d")
        
        url = f"https://www.elprisetjustnu.se/api/v1/prices/{year}/{date_str}_SE3.json"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="External pricing API failed")
            
            prices_data = response.json()
        
        for entry in prices_data:
            start_time = datetime.fromisoformat(entry["time_start"])
            existing = db.query(SpotPrice).filter(SpotPrice.time_start == start_time).first()
            if not existing:
                new_price = SpotPrice(
                    time_start=start_time,
                    sek_per_kwh=entry["SEK_per_kWh"],
                    zone="SE3"
                )
                db.add(new_price)
        db.commit()

    valid_prices = db.query(SpotPrice).filter(SpotPrice.time_start >= current_hour).all()
    
    if not valid_prices:
        raise HTTPException(status_code=500, detail="No valid prices available")

    best_hour = None
    lowest_cost = float('inf')

    for entry in valid_prices:
        cost = entry.sek_per_kwh * task.consumption_kwh
        if cost < lowest_cost:
            lowest_cost = cost
            best_hour = entry.time_start

    return {
        "cheapest_start_time": best_hour,
        "estimated_cost_sek": round(lowest_cost, 4),
        "zone": "SE3",
        "data_source": "postgresql_cache"
    }