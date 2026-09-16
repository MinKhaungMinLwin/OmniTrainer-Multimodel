from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.api.omni_api.audit import add_audit_event
from services.api.omni_api.auth import TenantContext, get_tenant_context, require_roles
from services.api.omni_api.database import get_session
from services.api.omni_api.models import (
    Appointment,
    AvailabilityWindow,
    CommandReceipt,
    Customer,
    CustomerLocation,
    Invoice,
    InvoiceLine,
    Job,
    JobStatusHistory,
    OutboxEvent,
    Technician,
)
from services.api.omni_api.schemas import (
    AppointmentRead,
    CompleteJob,
    DraftInvoice,
    InvoiceCommand,
    InvoiceList,
    InvoiceRead,
    JobCreate,
    JobList,
    JobRead,
    ScheduleJob,
)

router = APIRouter(tags=["operations"])


def conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def tenant_job(session: AsyncSession, tenant_id: str, job_id: str) -> Job:
    job = await session.scalar(select(Job).where(Job.id == job_id, Job.tenant_id == tenant_id).with_for_update())
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def tenant_invoice(session: AsyncSession, tenant_id: str, invoice_id: str) -> Invoice:
    invoice = await session.scalar(
        select(Invoice)
        .options(selectinload(Invoice.lines))
        .where(Invoice.id == invoice_id, Invoice.tenant_id == tenant_id)
        .with_for_update()
    )
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


async def prior_resource(
    session: AsyncSession, tenant_id: str, idempotency_key: str, command: str
) -> CommandReceipt | None:
    receipt = await session.scalar(
        select(CommandReceipt).where(
            CommandReceipt.tenant_id == tenant_id,
            CommandReceipt.idempotency_key == idempotency_key,
        )
    )
    if receipt is not None and receipt.command != command:
        raise conflict("Idempotency key was already used for a different command")
    return receipt


def record_change(
    session: AsyncSession,
    *,
    context: TenantContext,
    request: Request,
    event_type: str,
    resource_type: str,
    resource_id: str,
    payload: dict | None = None,
) -> None:
    data = payload or {}
    add_audit_event(
        session,
        tenant_id=context.tenant_id,
        actor_user_id=context.user.id,
        action=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=request.state.correlation_id,
        payload=data,
    )
    session.add(
        OutboxEvent(
            tenant_id=context.tenant_id,
            event_type=event_type,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            payload=data,
            occurred_at=datetime.now(timezone.utc),
            correlation_id=request.state.correlation_id,
        )
    )


def add_receipt(
    session: AsyncSession,
    context: TenantContext,
    idempotency_key: str,
    command: str,
    resource_type: str,
    resource_id: str,
) -> None:
    session.add(
        CommandReceipt(
            tenant_id=context.tenant_id,
            idempotency_key=idempotency_key,
            command=command,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )


def add_job_status(
    session: AsyncSession,
    context: TenantContext,
    job_id: str,
    from_status: str | None,
    to_status: str,
) -> None:
    session.add(
        JobStatusHistory(
            tenant_id=context.tenant_id,
            job_id=job_id,
            from_status=from_status,
            to_status=to_status,
            occurred_at=datetime.now(timezone.utc),
            actor_user_id=context.user.id,
        )
    )


async def resolve_technician(
    session: AsyncSession,
    tenant_id: str,
    technician_id: str,
    starts_at: datetime,
    ends_at: datetime,
) -> Technician:
    technician = await session.scalar(
        select(Technician).where(
            Technician.id == technician_id,
            Technician.tenant_id == tenant_id,
            Technician.is_active.is_(True),
        )
    )
    if technician is None:
        raise HTTPException(status_code=404, detail="Active technician not found")
    try:
        local_start = starts_at.astimezone(ZoneInfo(technician.timezone))
        local_end = ends_at.astimezone(ZoneInfo(technician.timezone))
    except ZoneInfoNotFoundError:
        raise HTTPException(status_code=422, detail="Technician timezone is invalid") from None
    start_minute = local_start.hour * 60 + local_start.minute
    end_minute = local_end.hour * 60 + local_end.minute
    available = await session.scalar(
        select(AvailabilityWindow.id).where(
            AvailabilityWindow.tenant_id == tenant_id,
            AvailabilityWindow.technician_id == technician.id,
            AvailabilityWindow.weekday == local_start.weekday(),
            AvailabilityWindow.start_minute <= start_minute,
            AvailabilityWindow.end_minute >= end_minute,
        )
    )
    if local_end.date() != local_start.date() or available is None:
        raise conflict("Appointment falls outside technician availability")
    return technician


@router.get("/jobs", response_model=JobList)
async def list_jobs(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
    status_filter: str | None = Query(default=None, alias="status"),
) -> JobList:
    filters = [Job.tenant_id == context.tenant_id]
    if status_filter:
        filters.append(Job.status == status_filter)
    total = await session.scalar(select(func.count(Job.id)).where(*filters))
    jobs = await session.scalars(select(Job).where(*filters).order_by(Job.created_at.desc()))
    return JobList(items=[JobRead.model_validate(job) for job in jobs], total=total or 0)


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> Job:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "job.create")
    if prior:
        return await tenant_job(session, context.tenant_id, prior.resource_id)
    customer = await session.scalar(
        select(Customer).where(Customer.id == payload.customer_id, Customer.tenant_id == context.tenant_id)
    )
    if customer is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.location_id:
        location = await session.scalar(
            select(CustomerLocation).where(
                CustomerLocation.id == payload.location_id,
                CustomerLocation.customer_id == customer.id,
                CustomerLocation.tenant_id == context.tenant_id,
            )
        )
        if location is None:
            raise HTTPException(status_code=404, detail="Customer location not found")
    job = Job(tenant_id=context.tenant_id, **payload.model_dump())
    session.add(job)
    await session.flush()
    add_job_status(session, context, job.id, None, "draft")
    add_receipt(session, context, idempotency_key, "job.create", "job", job.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.created",
        resource_type="job",
        resource_id=job.id,
        payload={"customer_id": job.customer_id, "title": job.title},
    )
    await session.commit()
    await session.refresh(job)
    return job


@router.post("/jobs/{job_id}/schedule", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
async def schedule_job(
    job_id: str,
    payload: ScheduleJob,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "dispatcher")),
    session: AsyncSession = Depends(get_session),
) -> Appointment:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "job.schedule")
    if prior:
        appointment = await session.scalar(
            select(Appointment).where(Appointment.id == prior.resource_id, Appointment.tenant_id == context.tenant_id)
        )
        if appointment:
            return appointment
    if payload.starts_at.tzinfo is None or payload.ends_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="Appointment timestamps must include a UTC offset")
    if payload.ends_at <= payload.starts_at:
        raise HTTPException(status_code=422, detail="Appointment end must be after its start")
    job = await tenant_job(session, context.tenant_id, job_id)
    if job.version != payload.expected_version:
        raise conflict("Job version is stale; reload before scheduling")
    if job.status != "draft":
        raise conflict("Only draft jobs can be scheduled")
    starts_at = payload.starts_at.astimezone(timezone.utc)
    ends_at = payload.ends_at.astimezone(timezone.utc)
    technician = None
    if payload.technician_id:
        technician = await resolve_technician(session, context.tenant_id, payload.technician_id, starts_at, ends_at)
    if payload.assignee:
        overlap = await session.scalar(
            select(Appointment.id).where(
                Appointment.tenant_id == context.tenant_id,
                Appointment.assignee == payload.assignee,
                Appointment.status == "scheduled",
                Appointment.starts_at < ends_at,
                Appointment.ends_at > starts_at,
            )
        )
        if overlap:
            raise conflict("Assignee already has an overlapping appointment")
    if technician:
        overlap = await session.scalar(
            select(Appointment.id).where(
                Appointment.tenant_id == context.tenant_id,
                Appointment.technician_id == technician.id,
                Appointment.status == "scheduled",
                Appointment.starts_at < ends_at,
                Appointment.ends_at > starts_at,
            )
        )
        if overlap:
            raise conflict("Technician already has an overlapping appointment")
    appointment = Appointment(
        tenant_id=context.tenant_id,
        job_id=job.id,
        starts_at=starts_at,
        ends_at=ends_at,
        timezone=payload.timezone,
        assignee=technician.name if technician else payload.assignee,
        technician_id=technician.id if technician else None,
    )
    session.add(appointment)
    job.status = "scheduled"
    job.version += 1
    add_job_status(session, context, job.id, "draft", "scheduled")
    await session.flush()
    add_receipt(session, context, idempotency_key, "job.schedule", "appointment", appointment.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.scheduled",
        resource_type="job",
        resource_id=job.id,
        payload={"appointment_id": appointment.id, "starts_at": appointment.starts_at.isoformat()},
    )
    await session.commit()
    await session.refresh(appointment)
    return appointment


@router.post("/jobs/{job_id}/complete", response_model=JobRead)
async def complete_job(
    job_id: str,
    payload: CompleteJob,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "dispatcher", "technician")),
    session: AsyncSession = Depends(get_session),
) -> Job:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "job.complete")
    if prior:
        return await tenant_job(session, context.tenant_id, prior.resource_id)
    job = await tenant_job(session, context.tenant_id, job_id)
    if job.version != payload.expected_version:
        raise conflict("Job version is stale; reload before completing")
    if job.status != "scheduled":
        raise conflict("Only scheduled jobs can be completed")
    job.status = "completed"
    job.version += 1
    job.completed_at = datetime.now(timezone.utc)
    add_job_status(session, context, job.id, "scheduled", "completed")
    add_receipt(session, context, idempotency_key, "job.complete", "job", job.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="job.completed",
        resource_type="job",
        resource_id=job.id,
    )
    await session.commit()
    await session.refresh(job)
    return job


@router.get("/appointments", response_model=list[AppointmentRead])
async def list_appointments(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> list[Appointment]:
    result = await session.scalars(
        select(Appointment).where(Appointment.tenant_id == context.tenant_id).order_by(Appointment.starts_at)
    )
    return list(result)


@router.get("/invoices", response_model=InvoiceList)
async def list_invoices(
    context: TenantContext = Depends(get_tenant_context),
    session: AsyncSession = Depends(get_session),
) -> InvoiceList:
    total = await session.scalar(select(func.count(Invoice.id)).where(Invoice.tenant_id == context.tenant_id))
    result = await session.scalars(
        select(Invoice)
        .options(selectinload(Invoice.lines))
        .where(Invoice.tenant_id == context.tenant_id)
        .order_by(Invoice.created_at.desc())
    )
    return InvoiceList(items=[InvoiceRead.model_validate(invoice) for invoice in result], total=total or 0)


@router.post("/jobs/{job_id}/invoice", response_model=InvoiceRead, status_code=status.HTTP_201_CREATED)
async def draft_invoice(
    job_id: str,
    payload: DraftInvoice,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "accountant")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "invoice.draft")
    if prior:
        return await tenant_invoice(session, context.tenant_id, prior.resource_id)
    job = await tenant_job(session, context.tenant_id, job_id)
    if job.version != payload.expected_job_version:
        raise conflict("Job version is stale; reload before invoicing")
    if job.status != "completed":
        raise conflict("Only completed jobs can be invoiced")
    existing_invoice = await session.scalar(
        select(Invoice.id).where(
            Invoice.tenant_id == context.tenant_id,
            Invoice.job_id == job.id,
        )
    )
    if existing_invoice:
        raise conflict("This job already has an invoice")
    invoice_count = await session.scalar(select(func.count(Invoice.id)).where(Invoice.tenant_id == context.tenant_id))
    subtotal = sum(line.quantity * line.unit_price_cents for line in payload.lines)
    invoice = Invoice(
        tenant_id=context.tenant_id,
        customer_id=job.customer_id,
        job_id=job.id,
        number=f"INV-{(invoice_count or 0) + 1:05d}",
        currency=payload.currency.upper(),
        subtotal_cents=subtotal,
        total_cents=subtotal,
    )
    invoice.lines = [
        InvoiceLine(
            description=line.description,
            quantity=line.quantity,
            unit_price_cents=line.unit_price_cents,
            amount_cents=line.quantity * line.unit_price_cents,
        )
        for line in payload.lines
    ]
    session.add(invoice)
    await session.flush()
    add_receipt(session, context, idempotency_key, "invoice.draft", "invoice", invoice.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="invoice.drafted",
        resource_type="invoice",
        resource_id=invoice.id,
        payload={"job_id": job.id, "total_cents": subtotal},
    )
    await session.commit()
    return await tenant_invoice(session, context.tenant_id, invoice.id)


@router.post("/invoices/{invoice_id}/issue", response_model=InvoiceRead)
async def issue_invoice(
    invoice_id: str,
    payload: InvoiceCommand,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "accountant")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "invoice.issue")
    if prior:
        return await tenant_invoice(session, context.tenant_id, prior.resource_id)
    invoice = await tenant_invoice(session, context.tenant_id, invoice_id)
    if invoice.version != payload.expected_version:
        raise conflict("Invoice version is stale; reload before issuing")
    if invoice.status != "draft":
        raise conflict("Only draft invoices can be issued")
    invoice.status = "issued"
    invoice.version += 1
    invoice.issued_at = datetime.now(timezone.utc)
    job = await tenant_job(session, context.tenant_id, invoice.job_id)
    job.status = "invoiced"
    job.version += 1
    add_job_status(session, context, job.id, "completed", "invoiced")
    add_receipt(session, context, idempotency_key, "invoice.issue", "invoice", invoice.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="invoice.issued",
        resource_type="invoice",
        resource_id=invoice.id,
        payload={"number": invoice.number, "total_cents": invoice.total_cents},
    )
    await session.commit()
    return await tenant_invoice(session, context.tenant_id, invoice.id)


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceRead)
async def void_invoice(
    invoice_id: str,
    payload: InvoiceCommand,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    context: TenantContext = Depends(require_roles("owner", "accountant")),
    session: AsyncSession = Depends(get_session),
) -> Invoice:
    prior = await prior_resource(session, context.tenant_id, idempotency_key, "invoice.void")
    if prior:
        return await tenant_invoice(session, context.tenant_id, prior.resource_id)
    invoice = await tenant_invoice(session, context.tenant_id, invoice_id)
    if invoice.version != payload.expected_version:
        raise conflict("Invoice version is stale; reload before voiding")
    if invoice.status != "issued":
        raise conflict("Only issued invoices can be voided")
    if invoice.paid_cents:
        raise conflict("Paid invoices cannot be voided")
    invoice.status = "void"
    invoice.version += 1
    invoice.voided_at = datetime.now(timezone.utc)
    job = await tenant_job(session, context.tenant_id, invoice.job_id)
    job.status = "completed"
    job.version += 1
    add_job_status(session, context, job.id, "invoiced", "completed")
    add_receipt(session, context, idempotency_key, "invoice.void", "invoice", invoice.id)
    record_change(
        session,
        context=context,
        request=request,
        event_type="invoice.voided",
        resource_type="invoice",
        resource_id=invoice.id,
    )
    await session.commit()
    return await tenant_invoice(session, context.tenant_id, invoice.id)
