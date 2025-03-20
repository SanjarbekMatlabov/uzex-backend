# main.py
from fastapi import FastAPI, Depends, HTTPException, status, Query,WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List, Optional,Dict
from datetime import datetime, timedelta
import uvicorn
from auth import *
from database import get_db, engine
from models import Base, User, AutoPlate, Bid
from schemas import (
    UserCreate, User as UserSchema,
    AutoPlateCreate, AutoPlateUpdate, AutoPlateWithHighestBid, AutoPlateDetail,
    BidCreate, BidUpdate, BidDetail,
    Token
)
from fastapi.middleware.cors import CORSMiddleware
from auth import *
import json

Base.metadata.create_all(bind=engine)

app = FastAPI(title="AutoPlate Auction API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],  
    allow_headers=["*"],  
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, plate_id: int):
        await websocket.accept()
        if plate_id not in self.active_connections:
            self.active_connections[plate_id] = []
        self.active_connections[plate_id].append(websocket)

    def disconnect(self, websocket: WebSocket, plate_id: int):
        if plate_id in self.active_connections and websocket in self.active_connections[plate_id]:
            self.active_connections[plate_id].remove(websocket)
            if not self.active_connections[plate_id]:
                del self.active_connections[plate_id]

    async def broadcast(self, message: dict, plate_id: int):
        if plate_id in self.active_connections:
            for connection in self.active_connections[plate_id]:
                await connection.send_json(message)

manager = ConnectionManager()
@app.post("/login/", response_model=Token)
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.websocket("/ws/bids/{plate_id}/")
async def websocket_endpoint(websocket: WebSocket, plate_id: int, db: Session = Depends(get_db)):
    await manager.connect(websocket, plate_id)
    try:
        highest_bid = db.query(func.max(Bid.amount)).filter(Bid.plate_id == plate_id).scalar() or 0
        bids = db.query(Bid).filter(Bid.plate_id == plate_id).order_by(Bid.created_at.desc()).all()
        bid_list = [{"id": bid.id, "amount": bid.amount, "user_id": bid.user_id, "created_at": bid.created_at.isoformat()} for bid in bids]
        
        await websocket.send_json({
            "type": "initial",
            "highest_bid": highest_bid,
            "bids": bid_list
        })

        while True:
            await websocket.receive_text()  
    except WebSocketDisconnect:
        manager.disconnect(websocket, plate_id)
@app.post("/users/", response_model=UserSchema)
async def create_user(user: UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    
    db_email = db.query(User).filter(User.email == user.email).first()
    if db_email:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_password = get_password_hash(user.password)
    db_user = User(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
        is_staff=user.is_staff
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# Auto Plate Endpoints
@app.get("/users/me")
def read_users_me(current_user: User = Depends(get_current_user)):
    """
    Hozirgi foydalanuvchining ma'lumotlarini qaytaradi.
    """
    return current_user
@app.get("/plates/search/", response_model=List[AutoPlateWithHighestBid])
async def list_plates(
    ordering: Optional[str] = None,
    plate_number__contains: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(
        AutoPlate,
        func.max(Bid.amount).label("highest_bid")
    ).outerjoin(Bid).filter(AutoPlate.is_active == True).group_by(AutoPlate.id)
    
    if plate_number__contains:
        # Bo‘shliqlarni olib tashlash va qidiruvni moslashtirish
        search = plate_number__contains.replace(" ", "")
        query = query.filter(func.replace(AutoPlate.plate_number, " ", "").ilike(f"%{search}%"))
    
    if ordering == "deadline":
        query = query.order_by(AutoPlate.deadline)
    
    results = query.all()
    plate_list = []
    
    for plate, highest_bid in results:
        plate_dict = {
            "id": plate.id,
            "plate_number": plate.plate_number,
            "description": plate.description,
            "deadline": plate.deadline,
            "is_active": plate.is_active,
            "highest_bid": highest_bid,
            "bids": [bid.__dict__ for bid in plate.bids]  # Bid’larni qo‘shish
        }
        plate_list.append(plate_dict)
    
    return plate_list

@app.post("/plates/", response_model=AutoPlateDetail, status_code=status.HTTP_201_CREATED)
async def create_plate(
    plate: AutoPlateCreate,
    current_user: User = Depends(get_current_active_staff_user),
    db: Session = Depends(get_db)
):
    # Check if plate number already exists
    existing_plate = db.query(AutoPlate).filter(AutoPlate.plate_number == plate.plate_number).first()
    if existing_plate:
        raise HTTPException(status_code=400, detail="Plate number already exists")
    
    # Check if deadline is in the future
    if plate.deadline <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Deadline must be in the future")
    
    db_plate = AutoPlate(
        plate_number=plate.plate_number,
        description=plate.description,
        deadline=plate.deadline,
        created_by_id=current_user.id
    )
    db.add(db_plate)
    db.commit()
    db.refresh(db_plate)
    return db_plate

@app.get("/plates/{plate_id}/", response_model=AutoPlateDetail)
async def get_plate(plate_id: int, db: Session = Depends(get_db)):
    plate = db.query(AutoPlate).filter(AutoPlate.id == plate_id).first()
    if not plate:
        raise HTTPException(status_code=404, detail="Plate not found")
    
    highest_bid = db.query(func.max(Bid.amount)).filter(Bid.plate_id == plate_id).scalar()
    
    # Convert plate to dict and add highest_bid
    plate_dict = {
        "id": plate.id,
        "plate_number": plate.plate_number,
        "description": plate.description,
        "deadline": plate.deadline,
        "is_active": plate.is_active,
        "created_by_id": plate.created_by_id,
        "highest_bid": highest_bid,
        "bids": plate.bids
    }
    
    return plate_dict

@app.put("/plates/{plate_id}/", response_model=AutoPlateDetail)
async def update_plate(
    plate_id: int,
    plate_update: AutoPlateUpdate,
    current_user: User = Depends(get_current_active_staff_user),
    db: Session = Depends(get_db)
):
    db_plate = db.query(AutoPlate).filter(AutoPlate.id == plate_id).first()
    if not db_plate:
        raise HTTPException(status_code=404, detail="Plate not found")
    
    # Check if deadline is in the future
    if plate_update.deadline <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Deadline must be in the future")
    
    # Check if plate number already exists (if changing)
    if plate_update.plate_number != db_plate.plate_number:
        existing_plate = db.query(AutoPlate).filter(
            AutoPlate.plate_number == plate_update.plate_number
        ).first()
        if existing_plate:
            raise HTTPException(status_code=400, detail="Plate number already exists")
    
    db_plate.plate_number = plate_update.plate_number
    db_plate.description = plate_update.description
    db_plate.deadline = plate_update.deadline
    
    db.commit()
    db.refresh(db_plate)
    return db_plate

@app.delete("/plates/{plate_id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plate(
    plate_id: int,
    current_user: User = Depends(get_current_active_staff_user),
    db: Session = Depends(get_db)
):
    db_plate = db.query(AutoPlate).filter(AutoPlate.id == plate_id).first()
    if not db_plate:
        raise HTTPException(status_code=404, detail="Plate not found")
    
    # Check if plate has bids
    bids_count = db.query(Bid).filter(Bid.plate_id == plate_id).count()
    if bids_count > 0:
        raise HTTPException(status_code=400, detail="Cannot delete plate with active bids")
    
    db.delete(db_plate)
    db.commit()
    return None

# Bid Endpoints
@app.get("/bids/", response_model=List[BidDetail])
async def list_bids(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    bids = db.query(Bid).filter(Bid.user_id == current_user.id).all()
    return bids

@app.post("/bids/", response_model=BidDetail, status_code=status.HTTP_201_CREATED)
async def create_bid(
    bid: BidCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    plate = db.query(AutoPlate).filter(AutoPlate.id == bid.plate_id).first()
    if not plate:
        raise HTTPException(status_code=404, detail="Plate not found")
    
    if not plate.is_active:
        raise HTTPException(status_code=400, detail="Bidding is closed")
    
    if plate.deadline < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Bidding period has ended")
    
    if bid.amount <= 0:
        raise HTTPException(status_code=400, detail="Bid amount must be positive")

    highest_bid = db.query(func.max(Bid.amount)).filter(Bid.plate_id == bid.plate_id).scalar() or 0

    last_bid = db.query(Bid).filter(Bid.plate_id == bid.plate_id).order_by(Bid.created_at.desc()).first()

    if last_bid and last_bid.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Siz yangi bid qo‘yish uchun boshqa foydalanuvchi bid berishini kutishingiz kerak")

    if bid.amount <= highest_bid:
        raise HTTPException(status_code=400, detail="Bid must exceed current highest bid")

    db_bid = Bid(
        amount=bid.amount,
        user_id=current_user.id,
        plate_id=bid.plate_id
    )
    db.add(db_bid)
    db.commit()
    db.refresh(db_bid)

    # WebSocket orqali yangi bidni barcha ulangan clientlarga yuborish
    await manager.broadcast({
        "type": "new_bid",
        "bid": {
            "id": db_bid.id,
            "amount": db_bid.amount,
            "user_id": db_bid.user_id,
            "created_at": db_bid.created_at.isoformat()
        },
        "highest_bid": db_bid.amount
    }, bid.plate_id)

    return db_bid

@app.get("/bids/{bid_id}/", response_model=BidDetail)
async def get_bid(
    bid_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    
    if bid.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this bid")
    
    return bid

@app.post("/bids/", response_model=BidDetail, status_code=status.HTTP_201_CREATED)
async def create_bid(
    bid: BidCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    plate = db.query(AutoPlate).filter(AutoPlate.id == bid.plate_id).first()
    if not plate:
        raise HTTPException(status_code=404, detail="Plate not found")
    
    if not plate.is_active:
        raise HTTPException(status_code=400, detail="Bidding is closed")
    
    if plate.deadline < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Bidding period has ended")
    
    if bid.amount <= 0:
        raise HTTPException(status_code=400, detail="Bid amount must be positive")

    highest_bid = db.query(func.max(Bid.amount)).filter(Bid.plate_id == bid.plate_id).scalar() or 0

    last_bid = db.query(Bid).filter(Bid.plate_id == bid.plate_id).order_by(Bid.created_at.desc()).first()

    if last_bid and last_bid.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Siz yangi bid qo‘yish uchun boshqa foydalanuvchi bid berishini kutishingiz kerak")

    if bid.amount <= highest_bid:
        raise HTTPException(status_code=400, detail="Bid must exceed current highest bid")

    db_bid = Bid(
        amount=bid.amount,
        user_id=current_user.id,
        plate_id=bid.plate_id
    )
    db.add(db_bid)
    db.commit()
    db.refresh(db_bid)
    return db_bid



@app.delete("/bids/{bid_id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bid(
    bid_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="Bid not found")
    
    if bid.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this bid")
    
    plate = db.query(AutoPlate).filter(AutoPlate.id == bid.plate_id).first()
    if not plate:
        raise HTTPException(status_code=404, detail="Associated plate not found")
    
    if plate.deadline < datetime.utcnow():
        raise HTTPException(status_code=403, detail="Bidding period has ended")
    
    db.delete(bid)
    db.commit()

    new_highest_bid = db.query(func.max(Bid.amount)).filter(Bid.plate_id == plate.id).scalar() or 0
    
    await manager.broadcast({
        "type": "bid_deleted",
        "bid_id": bid_id,
        "new_highest_bid": new_highest_bid
    }, plate.id)

    return None