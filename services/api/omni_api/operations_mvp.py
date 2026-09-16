import asyncio
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.database import get_session
from services.api.omni_api.models import (
    Appointment,
    AuditEvent,
    AvailabilityWindow,
    Customer,
    CustomerContact,
    CustomerLocation,
    Invoice,
    InvoiceLine,
    Job,
    JobAttachment,
    JobNote,
    JobStatusHistory,
    Payment,
    Technician,
)
from services.api.omni_api.operations import (
    add_receipt,
    conflict,
    prior_resource,
    record_change,
    resolve_technician,
    tenant_invoice,
    tenant_job,
)
from services.api.omni_api.schemas import (
    ActivityRead,
    AppointmentRead,
    AttachmentRead,
    AvailabilityCreate,
    AvailabilityRead,
    ContactCreate,
    ContactRead,
    CustomerDetail,
    CustomerUpdate,
    InvoiceRead,
    JobNoteCreate,
    JobNoteRead,
    JobStatusRead,
    LocationCreate,
    LocationRead,
    PaymentCreate,
    PaymentRead,
    RescheduleAppointment,
    TechnicianCreate,
    TechnicianRead,
    UpdateInvoiceLines,
)
from services.api.omni_api.storage import AttachmentStorage

router = APIRouter(tags=["operations-mvp"])


async def tenant_customer(session: AsyncSession, tenant_id: str, customer_id: str) -> Customer:
    customer = await session.scalar(
        select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id).with_for_update()
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


async def tenant_technician(session: AsyncSession, tenant_id: str, technician_id: str) -> Technician:
    technician = await session.scalar(
        select(Technician).where(Technician.id == technician_id, Technician.tenant_id == tenant_id)
    )
    if technician is None:
        raise HTTPException(status_code=404, detail="Technician not found")
    return technician


@router.get("/customers/{customer_id}", response_model=CustomerDetail)
async def customer_detail(
    customer_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> CustomerDetail:
    customer = await tenant_customer(session, context.tenant_id, customer_id)
    contacts = await session.scalars(
        select(CustomerContact)
        .where(CustomerContact.customer_id == customer.id, CustomerContact.tenant_id == context.tenant_id)
        .order_by(CustomerContact.is_primary.desc(), CustomerContact.name)
    )
    locations = await session.scalars(
        select(CustomerLocation)
        .where(CustomerLocation.customer_id == customer.id, CustomerLocation.tenant_id == context.tenant_id)
        .order_by(CustomerLocation.label)
    )
    activity = await session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.tenant_id == context.tenant_id,
            or_(
                (AuditEvent.resource_type == "customer") & (AuditEvent.resource_id == customer.id),
                AuditEvent.payload["customer_id"].as_string() == customer.id,
            ),
        )
        .order_by(AuditEvent.occurred_at.desc())
        .limit(100)
    )
    return CustomerDetail(
        **customer.__dict__,
        contacts=[ContactRead.model_validate(item) for item in contacts],
        locations=[LocationRead.model_validate(item) for item in locations],
        activity=[ActivityRead.model_validate(item) for item in activity],
    )


@router.put("/customers/{customer_id}", response_model=CustomerDetail)
async def update_customer(
    customer_id: str,
    payload: CustomerUpdate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> CustomerDetail:
    customer = await tenant_customer(session, context.tenant_id, customer_id)
    if customer.version != payload.expected_version:
        raise conflict("Customer version is stale; reload before saving")
    for key, value in payload.model_dump(exclude={"expected_version"}).items():
        setattr(customer, key, value)
    customer.version += 1
    record_change(
        session,
        context=context,
        request=request,
        event_type="customer.updated",
        resource_type="customer",
        resource_id=customer.id,
        payload={"name": customer.name},
    )
    await session.commit()
    return await customer_detail(customer_id, context, session)


@router.delete("/customers/{customer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_customer(
    customer_id: str,
    request: Request,
    context: TenantContext = Depends(require_roles("owner")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    customer = await tenant_customer(session, context.tenant_id, customer_id)
    job_count = await session.scalar(select(func.count(Job.id)).where(Job.customer_id == customer.id))
    if job_count:
        raise conflict("Customers with jobs cannot be deleted")
    record_change(
        session,
        context=context,
        request=request,
        event_type="customer.deleted",
        resource_type="customer",
        resource_id=customer.id,
        payload={"name": customer.name},
    )
    await session.delete(customer)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/customers/{customer_id}/contacts", response_model=ContactRead, status_code=201)
async def add_contact(
    customer_id: str,
    payload: ContactCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> CustomerContact:
    await tenant_customer(session, context.tenant_id, customer_id)
    if payload.is_primary:
        existing = await session.scalars(
            select(CustomerContact).where(
                CustomerContact.customer_id == customer_id,
                CustomerContact.tenant_id == context.tenant_id,
            )
        )
        for contact in existing:
            contact.is_primary = False
    contact = CustomerContact(tenant_id=context.tenant_id, customer_id=customer_id, **payload.model_dump())
    session.add(contact)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="customer.contact_added",
        resource_type="customer",
        resource_id=customer_id,
        payload={"contact_id": contact.id, "name": contact.name},
    )
    await session.commit()
    await session.refresh(contact)
    return contact


@router.post("/customers/{customer_id}/locations", response_model=LocationRead, status_code=201)
async def add_location(
    customer_id: str,
    payload: LocationCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> CustomerLocation:
    await tenant_customer(session, context.tenant_id, customer_id)
    location = CustomerLocation(tenant_id=context.tenant_id, customer_id=customer_id, **payload.model_dump())
    session.add(location)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="customer.location_added",
        resource_type="customer",
        resource_id=customer_id,
        payload={"location_id": location.id, "label": location.label},
    )
    await session.commit()
    await session.refresh(location)
    return location


@router.get("/technicians", response_model=list[TechnicianRead])
async def list_technicians(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[Technician]:
    return list(
        await session.scalars(
            select(Technician).where(Technician.tenant_id == context.tenant_id).order_by(Technician.name)
        )
    )


@router.post("/technicians", response_model=TechnicianRead, status_code=201)
async def create_technician(
    payload: TechnicianCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> Technician:
    technician = Technician(tenant_id=context.tenant_id, **payload.model_dump())
    session.add(technician)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="technician.created",
        resource_type="technician",
        resource_id=technician.id,
        payload={"name": technician.name},
    )
    await session.commit()
    await session.refresh(technician)
    return technician


@router.post("/technicians/{technician_id}/availability", response_model=AvailabilityRead, status_code=201)
async def add_availability(
    technician_id: str,
    payload: AvailabilityCreate,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> AvailabilityWindow:
    await tenant_technician(session, context.tenant_id, technician_id)
    if payload.end_minute <= payload.start_minute:
        raise HTTPException(status_code=422, detail="Availability end must be after start")
    window = AvailabilityWindow(tenant_id=context.tenant_id, technician_id=technician_id, **payload.model_dump())
    session.add(window)
    await session.commit()
    await session.refresh(window)
    return window


@router.get("/technicians/{technician_id}/availability", response_model=list[AvailabilityRead])
async def list_availability(
    technician_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[AvailabilityWindow]:
    await tenant_technician(session, context.tenant_id, technician_id)
    return list(
        await session.scalars(
            select(AvailabilityWindow)
            .where(
                AvailabilityWindow.tenant_id == context.tenant_id,
                AvailabilityWindow.technician_id == technician_id,
            )
            .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_minute)
        )
    )


@router.post("/appointments/{appointment_id}/reschedule", response_model=AppointmentRead)
async def reschedule_appointment(
    appointment_id: str,
    payload: RescheduleAppointment,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> Appointment:
    appointment = await session.scalar(
        select(Appointment)
        .where(Appointment.id == appointment_id, Appointment.tenant_id == context.tenant_id)
        .with_for_update()
    )
    if appointment is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if appointment.version != payload.expected_version:
        raise conflict("Appointment version is stale; reload before rescheduling")
    if payload.starts_at.tzinfo is None or payload.ends_at.tzinfo is None or payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="Valid offset-aware appointment times are required")
    starts_at = payload.starts_at.astimezone(timezone.utc)
    ends_at = payload.ends_at.astimezone(timezone.utc)
    technician = None
    if payload.technician_id:
        technician = await resolve_technician(
            session,
            context.tenant_id,
            payload.technician_id,
            starts_at,
            ends_at,
        )
        overlap = await session.scalar(
            select(Appointment.id).where(
                Appointment.tenant_id == context.tenant_id,
                Appointment.technician_id == technician.id,
                Appointment.id != appointment.id,
                Appointment.status == "scheduled",
                Appointment.starts_at < ends_at,
                Appointment.ends_at > starts_at,
            )
        )
        if overlap:
            raise conflict("Technician already has an overlapping appointment")
    appointment.starts_at = starts_at
    appointment.ends_at = ends_at
    appointment.timezone = payload.timezone
    appointment.technician_id = technician.id if technician else None
    appointment.assignee = technician.name if technician else None
    appointment.version += 1
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.rescheduled",
        resource_type="job",
        resource_id=appointment.job_id,
        payload={"appointment_id": appointment.id, "starts_at": starts_at.isoformat()},
    )
    await session.commit()
    await session.refresh(appointment)
    return appointment


@router.post("/jobs/{job_id}/notes", response_model=JobNoteRead, status_code=201)
async def add_job_note(
    job_id: str,
    payload: JobNoteCreate,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "dispatcher", "technician")),
    session: AsyncSession = Depends(get_session),
) -> JobNote:
    await tenant_job(session, context.tenant_id, job_id)
    note = JobNote(
        tenant_id=context.tenant_id,
        job_id=job_id,
        author_user_id=context.user.id,
        body=payload.body,
    )
    session.add(note)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.note_added",
        resource_type="job",
        resource_id=job_id,
        payload={"note_id": note.id},
    )
    await session.commit()
    await session.refresh(note)
    return note


@router.get("/jobs/{job_id}/notes", response_model=list[JobNoteRead])
async def list_job_notes(
    job_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[JobNote]:
    await tenant_job(session, context.tenant_id, job_id)
    return list(
        await session.scalars(
            select(JobNote)
            .where(JobNote.tenant_id == context.tenant_id, JobNote.job_id == job_id)
            .order_by(JobNote.created_at.desc())
        )
    )


@router.get("/jobs/{job_id}/history", response_model=list[JobStatusRead])
async def job_history(
    job_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[JobStatusHistory]:
    await tenant_job(session, context.tenant_id, job_id)
    return list(
        await session.scalars(
            select(JobStatusHistory)
            .where(JobStatusHistory.tenant_id == context.tenant_id, JobStatusHistory.job_id == job_id)
            .order_by(JobStatusHistory.occurred_at)
        )
    )


@router.post("/jobs/{job_id}/attachments", response_model=AttachmentRead, status_code=201)
async def upload_attachment(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
    context: TenantContext = Depends(require_roles("owner", "dispatcher", "technician")),
    session: AsyncSession = Depends(get_session),
) -> JobAttachment:
    await tenant_job(session, context.tenant_id, job_id)
    content = await file.read(request.app.state.settings.max_attachment_bytes + 1)
    if len(content) > request.app.state.settings.max_attachment_bytes:
        raise HTTPException(status_code=413, detail="Attachment exceeds the configured size limit")
    filename = Path(file.filename or "attachment").name
    object_key = f"{context.tenant_id}/{job_id}/{uuid.uuid4()}-{filename}"
    storage = AttachmentStorage(request.app.state.settings)
    await asyncio.to_thread(storage.put, object_key, content, file.content_type or "application/octet-stream")
    attachment = JobAttachment(
        tenant_id=context.tenant_id,
        job_id=job_id,
        uploaded_by_user_id=context.user.id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
        object_key=object_key,
    )
    session.add(attachment)
    await session.flush()
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.attachment_added",
        resource_type="job",
        resource_id=job_id,
        payload={"attachment_id": attachment.id, "filename": filename},
    )
    await session.commit()
    await session.refresh(attachment)
    return attachment


@router.get("/jobs/{job_id}/attachments", response_model=list[AttachmentRead])
async def list_attachments(
    job_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[JobAttachment]:
    await tenant_job(session, context.tenant_id, job_id)
    return list(
        await session.scalars(
            select(JobAttachment)
            .where(JobAttachment.tenant_id == context.tenant_id, JobAttachment.job_id == job_id)
            .order_by(JobAttachment.created_at.desc())
        )
    )


@router.get("/attachments/{attachment_id}/download")
async def download_attachment(
    attachment_id: str,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    attachment = await session.scalar(
        select(JobAttachment).where(
            JobAttachment.id == attachment_id,
            JobAttachment.tenant_id == context.tenant_id,
        )
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    content = await asyncio.to_thread(AttachmentStorage(request.app.state.settings).get, attachment.object_key)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )


@router.put("/invoices/{invoice_id}/lines", response_model=InvoiceRead)
async def update_invoice_lines(
    invoice_id: str,
    payload: UpdateInvoiceLines,
    request: Request,
    context: TenantContext = Depends(require_roles("owner", "accountant")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    invoice = await tenant_invoice(session, context.tenant_id, invoice_id)
    if invoice.version != payload.expected_version:
        raise conflict("Invoice version is stale; reload before editing")
    if invoice.status != "draft":
        raise conflict("Only draft invoice lines can be edited")
    await session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
    subtotal = sum(item.quantity * item.unit_price_cents for item in payload.lines)
    invoice.lines = [
        InvoiceLine(
            description=item.description,
            quantity=item.quantity,
            unit_price_cents=item.unit_price_cents,
            amount_cents=item.quantity * item.unit_price_cents,
        )
        for item in payload.lines
    ]
    invoice.subtotal_cents = subtotal
    invoice.total_cents = subtotal
    invoice.version += 1
    record_change(
        session,
        context=context,
        request=request,
        event_type="invoice.lines_updated",
        resource_type="invoice",
        resource_id=invoice.id,
        payload={"total_cents": subtotal},
    )
    await session.commit()
    return await tenant_invoice(session, context.tenant_id, invoice.id)


@router.post("/invoices/{invoice_id}/payments", response_model=PaymentRead, status_code=201)
async def record_payment(
    invoice_id: str,
    payload: PaymentCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "accountant")),
    session: AsyncSession = Depends(get_session),
) -> Payment:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "invoice.payment")
    if prior:
        payment = await session.scalar(
            select(Payment).where(Payment.id == prior.resource_id, Payment.tenant_id == context.tenant_id)
        )
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        return payment
    invoice = await tenant_invoice(session, context.tenant_id, invoice_id)
    if invoice.status != "issued":
        raise conflict("Payments can only be recorded against issued invoices")
    if invoice.paid_cents + payload.amount_cents > invoice.total_cents:
        raise conflict("Payment exceeds the outstanding balance")
    if payload.received_at is not None and payload.received_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Payment timestamp must include a UTC offset")
    if payload.external_ref is not None:
        duplicate = await session.scalar(
            select(Payment.id).where(
                Payment.tenant_id == context.tenant_id,
                Payment.external_ref == payload.external_ref,
            )
        )
        if duplicate is not None:
            raise conflict("Payment external reference already exists")
    payment = Payment(
        tenant_id=context.tenant_id,
        invoice_id=invoice.id,
        amount_cents=payload.amount_cents,
        method=payload.method,
        external_ref=payload.external_ref,
        received_at=payload.received_at or datetime.now(timezone.utc),
    )
    session.add(payment)
    invoice.paid_cents += payment.amount_cents
    invoice.payment_status = "paid" if invoice.paid_cents == invoice.total_cents else "partial"
    invoice.version += 1
    await session.flush()
    add_receipt(session, context, idempotency_key, "invoice.payment", "payment", payment.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="invoice.payment_recorded",
        resource_type="invoice",
        resource_id=invoice.id,
        payload={"payment_id": payment.id, "amount_cents": payment.amount_cents},
    )
    await session.commit()
    await session.refresh(payment)
    return payment


@router.get("/invoices/{invoice_id}/payments", response_model=list[PaymentRead])
async def list_payments(
    invoice_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[Payment]:
    await tenant_invoice(session, context.tenant_id, invoice_id)
    return list(
        await session.scalars(
            select(Payment)
            .where(Payment.tenant_id == context.tenant_id, Payment.invoice_id == invoice_id)
            .order_by(Payment.received_at)
        )
    )


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(
    invoice_id: str,
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    invoice = await tenant_invoice(session, context.tenant_id, invoice_id)
    customer = await tenant_customer(session, context.tenant_id, invoice.customer_id)
    output = io.BytesIO()
    document = canvas.Canvas(output, pagesize=LETTER)
    document.setTitle(invoice.number)
    document.setFont("Helvetica-Bold", 20)
    document.drawString(54, 740, f"Invoice {invoice.number}")
    document.setFont("Helvetica", 11)
    document.drawString(54, 715, f"Customer: {customer.name}")
    document.drawString(54, 698, f"Status: {invoice.status.upper()}")
    y = 660
    for line in invoice.lines:
        document.drawString(54, y, f"{line.quantity} × {line.description}")
        document.drawRightString(558, y, f"{line.amount_cents / 100:,.2f} {invoice.currency}")
        y -= 22
    document.line(54, y, 558, y)
    document.setFont("Helvetica-Bold", 12)
    document.drawRightString(558, y - 24, f"Total: {invoice.total_cents / 100:,.2f} {invoice.currency}")
    document.drawRightString(558, y - 44, f"Paid: {invoice.paid_cents / 100:,.2f} {invoice.currency}")
    document.save()
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{invoice.number}.pdf"'},
    )
