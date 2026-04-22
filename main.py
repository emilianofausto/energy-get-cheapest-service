import httpx
from fastapi import FastAPI, HTTPException, Depends, Body
from pydantic import BaseModel
from datetime import datetime, timezone, timedelta
from typing import List
import os

from sqlalchemy import create_engine, Column, Integer, Float, DateTime, String
from sqlalchemy.orm import sessionmaker, Session, declarative_base

# Database Configuration
# Defaults to localhost for local testing. Will be overridden by K8s environment variables later.
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/energyprices")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------
# Database Models
# ---------------------------------------------------------
class SpotPrice(Base):
    __tablename__ = "spot_prices"
    
    id = Column(Integer, primary_key=True, index=True)
    time_start = Column(DateTime(timezone=True), unique=True, index=True)
    sek_per_kwh = Column(Float, nullable=False)
    zone = Column(String, index=True)

# Create tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Energy Cost API with PostgreSQL Cache")

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------------------------------------------------------
# Pydantic Schemas for validation
# ---------------------------------------------------------
class ApplianceTask(BaseModel):
    consumption_kwh: float
    duration_mins: int

class PriceResponse(BaseModel):
    id: int
    time_start: datetime
    sek_per_kwh: float
    zone: str

    class Config:
        from_attributes = True

# ---------------------------------------------------------
# CRUD Endpoints (Strict VG Requirement)
# ---------------------------------------------------------
@app.get("/prices", response_model=List[PriceResponse])
def get_all_prices(db: Session = Depends(get_db)):
    return db.query(SpotPrice).all()

@app.delete("/prices", status_code=200)
def clear_all_prices(db: Session = Depends(get_db)):
    db.query(SpotPrice).delete()
    db.commit()
    return {"message": "All cached prices deleted"}

# Add these imports if not present
from fastapi import Body

# 1. Manual Refresh (Trigger Fetch)
@app.get("/prices/refresh")
async def refresh_prices(db: Session = Depends(get_db)):
    # This triggers the logic already inside calculate-cost but as a standalone action
    await calculate_cheapest_time(ApplianceTask(consumption_kwh=0, duration_mins=0), db)
    return {"message": "Prices updated from external API"}

# 2. Create Manual Price
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

# 3. Delete Specific Price
@app.delete("/prices/{price_id}")
def delete_price(price_id: int, db: Session = Depends(get_db)):
    price = db.query(SpotPrice).filter(SpotPrice.id == price_id).first()
    if not price:
        raise HTTPException(status_code=404, detail="Price not found")
    db.delete(price)
    db.commit()
    return {"message": "Price deleted"}

# ---------------------------------------------------------
# Business Logic & Caching Endpoint
# ---------------------------------------------------------
@app.post("/calculate-cost")
async def calculate_cheapest_time(task: ApplianceTask, db: Session = Depends(get_db)):
    # 1. Determine current hour in UTC to check cache
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    
    # 2. Check if we have data for the current hour in the database (Cache hit)
    cached_price = db.query(SpotPrice).filter(SpotPrice.time_start == current_hour).first()
    
    if not cached_price:
        # Cache miss: Data is missing or outdated. Fetch from external API.
        today_local = datetime.now()
        year = today_local.strftime("%Y")
        date_str = today_local.strftime("%m-%d")
        
        # Hardcoded to SE3 (Stockholm/Tyresö region)
        url = f"https://www.elprisetjustnu.se/api/v1/prices/{year}/{date_str}_SE3.json"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise HTTPException(status_code=502, detail="External pricing API failed")
            
            prices_data = response.json()
        
        # 3. Store new data in the PostgreSQL database
        for entry in prices_data:
            start_time = datetime.fromisoformat(entry["time_start"])
            
            # Upsert logic to avoid duplicate key errors
            existing = db.query(SpotPrice).filter(SpotPrice.time_start == start_time).first()
            if not existing:
                new_price = SpotPrice(
                    time_start=start_time,
                    sek_per_kwh=entry["SEK_per_kWh"],
                    zone="SE3"
                )
                db.add(new_price)
        
        db.commit()

    # 4. Fetch all future valid prices from the database for calculation
    valid_prices = db.query(SpotPrice).filter(SpotPrice.time_start >= current_hour).all()
    
    if not valid_prices:
        raise HTTPException(status_code=500, detail="No valid prices available for calculation")

    # 5. Algorithm to find the cheapest time
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