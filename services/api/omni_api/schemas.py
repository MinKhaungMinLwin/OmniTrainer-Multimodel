from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    display_name: str


class TenantRead(BaseModel):
    id: str
    name: str
    slug: str
    role: str


class DevTokenRequest(BaseModel):
    email: EmailStr = "owner@omni.example"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    external_ref: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


class CustomerRead(CustomerCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    version: int
    created_at: datetime
    updated_at: datetime


class CustomerList(BaseModel):
    items: list[CustomerRead]
    total: int


class CustomerUpdate(CustomerCreate):
    expected_version: int = Field(ge=1)


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    role: str | None = Field(default=None, max_length=100)
    is_primary: bool = False


class ContactRead(ContactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    created_at: datetime
    updated_at: datetime


class LocationCreate(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    address_line1: str = Field(min_length=1, max_length=300)
    address_line2: str | None = Field(default=None, max_length=300)
    city: str = Field(min_length=1, max_length=150)
    region: str | None = Field(default=None, max_length=150)
    postal_code: str | None = Field(default=None, max_length=30)
    country: str = Field(default="US", min_length=2, max_length=2)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class LocationRead(LocationCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    created_at: datetime
    updated_at: datetime


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    resource_type: str
    resource_id: str
    occurred_at: datetime
    correlation_id: str
    payload: dict


class CustomerDetail(CustomerRead):
    contacts: list[ContactRead]
    locations: list[LocationRead]
    activity: list[ActivityRead]


class TechnicianCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=50)
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class TechnicianRead(TechnicianCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AvailabilityCreate(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start_minute: int = Field(ge=0, le=1439)
    end_minute: int = Field(ge=1, le=1440)


class AvailabilityRead(AvailabilityCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    technician_id: str


class JobCreate(BaseModel):
    customer_id: str
    location_id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


class JobRead(JobCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    status: Literal["draft", "scheduled", "completed", "invoiced"]
    version: int
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobList(BaseModel):
    items: list[JobRead]
    total: int


class ScheduleJob(BaseModel):
    starts_at: datetime
    ends_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    assignee: str | None = Field(default=None, max_length=200)
    technician_id: str | None = None
    expected_version: int = Field(ge=1)


class CompleteJob(BaseModel):
    expected_version: int = Field(ge=1)


class RescheduleAppointment(BaseModel):
    starts_at: datetime
    ends_at: datetime
    timezone: str = Field(default="UTC", min_length=1, max_length=100)
    technician_id: str | None = None
    expected_version: int = Field(ge=1)


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    job_id: str
    technician_id: str | None
    starts_at: datetime
    ends_at: datetime
    timezone: str
    assignee: str | None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class InvoiceLineCreate(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=10000)
    unit_price_cents: int = Field(ge=0, le=100_000_000)


class DraftInvoice(BaseModel):
    expected_job_version: int = Field(ge=1)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    lines: list[InvoiceLineCreate] = Field(min_length=1, max_length=100)


class InvoiceCommand(BaseModel):
    expected_version: int = Field(ge=1)


class UpdateInvoiceLines(BaseModel):
    expected_version: int = Field(ge=1)
    lines: list[InvoiceLineCreate] = Field(min_length=1, max_length=100)


class InvoiceLineRead(InvoiceLineCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    amount_cents: int


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    customer_id: str
    job_id: str
    number: str
    status: Literal["draft", "issued", "void"]
    currency: str
    subtotal_cents: int
    total_cents: int
    paid_cents: int
    payment_status: Literal["unpaid", "partial", "paid"]
    version: int
    issued_at: datetime | None
    voided_at: datetime | None
    lines: list[InvoiceLineRead]
    created_at: datetime
    updated_at: datetime


class InvoiceList(BaseModel):
    items: list[InvoiceRead]
    total: int


class PaymentCreate(BaseModel):
    amount_cents: int = Field(gt=0, le=100_000_000)
    method: str = Field(min_length=1, max_length=50)
    external_ref: str | None = Field(default=None, max_length=150)
    received_at: datetime | None = None


class PaymentRead(PaymentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    invoice_id: str
    received_at: datetime
    created_at: datetime
    updated_at: datetime


class JobNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class JobNoteRead(JobNoteCreate):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    author_user_id: str | None
    created_at: datetime
    updated_at: datetime


class JobStatusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    from_status: str | None
    to_status: str
    occurred_at: datetime
    actor_user_id: str | None


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime
