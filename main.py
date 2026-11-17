import uvicorn
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel

# ==========================================
# DATABASE SETUP (Using Old DB Structure)
# ==========================================
SQLALCHEMY_DATABASE_URL = "sqlite:///./hostel_database.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class AdminDB(Base):
    __tablename__ = "admin"
    username = Column(String, primary_key=True, index=True)
    password = Column(String)

class RoomDB(Base):
    __tablename__ = "rooms"
    room_number = Column(String, primary_key=True, index=True)
    capacity = Column(Integer)
    occupancy = Column(Integer, default=0)

class ResidentDB(Base):
    __tablename__ = "residents"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, unique=True)
    room_number = Column(String, ForeignKey("rooms.room_number"))

class PaymentDB(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True, index=True)
    resident_id = Column(Integer, ForeignKey("residents.id"))
    amount = Column(Float)
    month = Column(String)
    status = Column(String, default="Pending")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# FASTAPI APP SETUP
# ==========================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    db = SessionLocal()
    if not db.query(AdminDB).filter(AdminDB.username == "admin").first():
        db.add(AdminDB(username="admin", password="admin123"))
        db.commit()
    db.close()
    yield

api = FastAPI(title="Hostel Management API", lifespan=lifespan)

api.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Schemas ---
class RoomCreate(BaseModel):
    room_number: str
    capacity: int

class RoomUpdate(BaseModel):
    capacity: int

class ResidentCreate(BaseModel):
    name: str
    room_number: str

class ResidentUpdate(BaseModel):
    name: Optional[str] = None
    room_number: Optional[str] = None

class PaymentCreate(BaseModel):
    resident_id: int
    amount: float
    month: str
    status: str = "Paid"

class AdminLogin(BaseModel):
    username: str
    password: str

class AdminUpdatePwd(BaseModel):
    old_password: str
    new_password: str

# ==========================================
# ROUTES
# ==========================================
@api.get("/")
def serve_frontend():
    return FileResponse("index.html")

@api.post("/admin/login")
def login_admin(creds: AdminLogin, db: Session = Depends(get_db)):
    admin = db.query(AdminDB).filter(AdminDB.username == creds.username, AdminDB.password == creds.password).first()
    return {"success": bool(admin)}

@api.put("/admin/password")
def update_admin_password(data: AdminUpdatePwd, db: Session = Depends(get_db)):
    admin = db.query(AdminDB).filter(AdminDB.username == "admin", AdminDB.password == data.old_password).first()
    if admin:
        admin.password = data.new_password
        db.commit()
        return {"success": True}
    raise HTTPException(status_code=400, detail="Incorrect current password")

@api.post("/rooms/")
def create_room(room: RoomCreate, db: Session = Depends(get_db)):
    if db.query(RoomDB).filter(RoomDB.room_number == room.room_number).first():
        raise HTTPException(status_code=400, detail="Room number already exists")
    db_room = RoomDB(**room.model_dump())
    db.add(db_room)
    db.commit()
    return db_room

@api.put("/rooms/{room_number}")
def update_room(room_number: str, room_update: RoomUpdate, db: Session = Depends(get_db)):
    db_room = db.query(RoomDB).filter(RoomDB.room_number == room_number).first()
    if not db_room:
        raise HTTPException(status_code=404, detail="Room not found")
    
    if room_update.capacity < db_room.occupancy:
        raise HTTPException(status_code=400, detail=f"Cannot reduce capacity below current occupancy ({db_room.occupancy}). Remove residents first.")
        
    db_room.capacity = room_update.capacity
    db.commit()
    return db_room

@api.get("/rooms/")
def get_rooms(db: Session = Depends(get_db)):
    return db.query(RoomDB).order_by(RoomDB.room_number).all()

# Bulk Resident Addition
@api.post("/residents/bulk")
def add_multiple_residents(residents: List[ResidentCreate], db: Session = Depends(get_db)):
    names = [r.name for r in residents]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="Duplicate names provided in the form.")

    for res in residents:
        if db.query(ResidentDB).filter(ResidentDB.name.ilike(res.name)).first():
            raise HTTPException(status_code=400, detail=f"Resident '{res.name}' already exists in the system.")

    room_requirements = {}
    for res in residents:
        room_requirements[res.room_number] = room_requirements.get(res.room_number, 0) + 1

    for r_num, needed_beds in room_requirements.items():
        room = db.query(RoomDB).filter(RoomDB.room_number == r_num).first()
        if not room:
            raise HTTPException(status_code=404, detail=f"Room '{r_num}' not found.")
        available_beds = room.capacity - room.occupancy
        if needed_beds > available_beds:
            raise HTTPException(status_code=400, detail=f"Not enough beds in Room '{r_num}'. Needed: {needed_beds}, Available: {available_beds}.")

    added_residents = []
    for res in residents:
        room = db.query(RoomDB).filter(RoomDB.room_number == res.room_number).first()
        db_resident = ResidentDB(**res.model_dump())
        room.occupancy += 1
        db.add(db_resident)
        added_residents.append(db_resident)

    db.commit()
    return {"success": True, "added_count": len(added_residents)}

@api.get("/residents/")
def get_residents(db: Session = Depends(get_db)):
    return db.query(ResidentDB).order_by(ResidentDB.room_number).all()

@api.put("/residents/{resident_id}")
def update_resident(resident_id: int, resident_update: ResidentUpdate, db: Session = Depends(get_db)):
    db_resident = db.query(ResidentDB).filter(ResidentDB.id == resident_id).first()
    if not db_resident:
        raise HTTPException(status_code=404, detail="Resident not found")
        
    if resident_update.name and resident_update.name != db_resident.name:
        if db.query(ResidentDB).filter(ResidentDB.name.ilike(resident_update.name)).first():
             raise HTTPException(status_code=400, detail=f"Resident with name '{resident_update.name}' already exists.")
        db_resident.name = resident_update.name
    
    if resident_update.room_number and resident_update.room_number != db_resident.room_number:
        new_room = db.query(RoomDB).filter(RoomDB.room_number == resident_update.room_number).first()
        if not new_room or new_room.occupancy >= new_room.capacity:
            raise HTTPException(status_code=400, detail="New room full")
        
        old_room = db.query(RoomDB).filter(RoomDB.room_number == db_resident.room_number).first()
        if old_room: old_room.occupancy -= 1
        new_room.occupancy += 1
        db_resident.room_number = resident_update.room_number

    db.commit()
    return db_resident

@api.delete("/residents/{resident_id}")
def delete_resident(resident_id: int, db: Session = Depends(get_db)):
    db_resident = db.query(ResidentDB).filter(ResidentDB.id == resident_id).first()
    if not db_resident:
        raise HTTPException(status_code=404, detail="Resident not found")
    
    room = db.query(RoomDB).filter(RoomDB.room_number == db_resident.room_number).first()
    if room and room.occupancy > 0:
        room.occupancy -= 1
        
    db.query(PaymentDB).filter(PaymentDB.resident_id == resident_id).delete()
    
    db.delete(db_resident)
    db.commit()
    return {"success": True}

@api.post("/payments/")
def record_payment(payment: PaymentCreate, db: Session = Depends(get_db)):
    existing = db.query(PaymentDB).filter(
        PaymentDB.resident_id == payment.resident_id,
        PaymentDB.month == payment.month
    ).first()

    if existing:
        existing.amount = payment.amount
        existing.status = payment.status
        db.commit()
        return existing
    else:
        db_payment = PaymentDB(**payment.model_dump())
        db.add(db_payment)
        db.commit()
        return db_payment

@api.get("/payments/")
def get_payments(db: Session = Depends(get_db)):
    return db.query(PaymentDB).all()

@api.get("/payments/resident/{resident_id}")
def get_resident_payments(resident_id: int, db: Session = Depends(get_db)):
    return db.query(PaymentDB).filter(PaymentDB.resident_id == resident_id).all()

@api.delete("/payments/{payment_id}")
def delete_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(PaymentDB).filter(PaymentDB.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    db.delete(payment)
    db.commit()
    return {"success": True}

if __name__ == "__main__":
    uvicorn.run("main:api", host="127.0.0.1", port=8000, reload=True)