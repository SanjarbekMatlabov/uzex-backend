from pydantic import BaseModel, validator, Field
from datetime import datetime
from typing import List, Optional

# User schemas
class UserBase(BaseModel):
    username: str
    email: str

class UserCreate(UserBase):
    password: str
    is_staff: Optional[bool] = False

class User(UserBase):
    id: int
    is_staff: bool

    class Config:
        from_attributes = True

# AutoPlate schemas
class AutoPlateBase(BaseModel):
    plate_number: str
    description: str
    deadline: datetime

class AutoPlateCreate(AutoPlateBase):
    @validator('deadline')
    def deadline_must_be_future(cls, v):
        if v <= datetime.utcnow():
            raise ValueError('Deadline must be in the future')
        return v

class AutoPlateUpdate(AutoPlateBase):
    pass

class AutoPlateWithHighestBid(AutoPlateBase):
    id: int
    is_active: bool
    highest_bid: Optional[float] = None

    class Config:
        from_attributes = True

class AutoPlateDetail(AutoPlateWithHighestBid):
    created_by_id: int
    bids: List['BidDetail'] = []

    class Config:
        from_attributes = True

# Bid schemas
class BidBase(BaseModel):
    amount: float = Field(..., gt=0)

class BidCreate(BidBase):
    plate_id: int

class BidUpdate(BidBase):
    pass

class BidDetail(BidBase):
    id: int
    plate_id: int
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True

# Authentication schemas
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    username: Optional[str] = None

AutoPlateDetail.update_forward_refs()