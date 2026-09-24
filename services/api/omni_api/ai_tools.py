import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.api.omni_api.auth import TenantContext
from services.api.omni_api.customers import create_customer as create_customer_record
from services.api.omni_api.models import (
    AvailabilityWindow,
    Customer,
    Invoice,
    Job,
    JobStatusHistory,
    KnowledgeChunk,
    KnowledgeDocument,
    OutboundMessage,
    OutboxEvent,
    Technician,
)
from services.api.omni_api.operations import create_job, draft_invoice, schedule_job
from services.api.omni_api.operations_mvp import add_job_note
from services.api.omni_api.operations_mvp import create_technician as create_technician_record
from services.api.omni_api.schemas import (
    CustomerCreate,
    CustomerRead,
    DraftInvoice,
    InvoiceRead,
    JobCreate,
    JobNoteCreate,
    JobRead,
    ScheduleJob,
    TechnicianCreate,
    TechnicianRead,
)


class ToolInputError(ValueError):
    pass


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    title: str
    description: str
    risk: str
    roles: tuple[str, ...]
    input_schema: dict[str, Any]
    timeout_seconds: int = 10
    version: str = "1"


def object_schema(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": required, "properties": properties}


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "find_customer": ToolDefinition(
        "find_customer",
        "Find customers",
        "Search tenant customers by name or email.",
        "read",
        ("owner", "dispatcher", "technician", "accountant", "reviewer", "administrator"),
        object_schema(["query"], {"query": {"type": "string", "minLength": 1}}),
    ),
    "fetch_job_history": ToolDefinition(
        "fetch_job_history",
        "Fetch job history",
        "Read the append-only lifecycle history for one job.",
        "read",
        ("owner", "dispatcher", "technician", "accountant", "reviewer", "administrator"),
        object_schema(["job_id"], {"job_id": {"type": "string", "format": "uuid"}}),
    ),
    "check_availability": ToolDefinition(
        "check_availability",
        "Check availability",
        "List active technicians and their configured availability windows.",
        "read",
        ("owner", "dispatcher", "technician", "reviewer", "administrator"),
        object_schema([], {"query": {"type": "string"}}),
    ),
    "retrieve_invoice": ToolDefinition(
        "retrieve_invoice",
        "Retrieve invoice",
        "Read an invoice and its line items.",
        "read",
        ("owner", "dispatcher", "accountant", "reviewer", "administrator"),
        object_schema(["invoice_id"], {"invoice_id": {"type": "string", "format": "uuid"}}),
    ),
    "search_knowledge": ToolDefinition(
        "search_knowledge",
        "Search knowledge",
        "Search authorized policy and knowledge chunks and return citations.",
        "read",
        ("owner", "dispatcher", "technician", "accountant", "reviewer", "administrator"),
        object_schema(["query"], {"query": {"type": "string", "minLength": 1}}),
    ),
    "create_customer": ToolDefinition(
        "create_customer",
        "Create customer",
        "Add a customer to the tenant customer list after human approval.",
        "write",
        ("owner", "dispatcher"),
        object_schema(
            ["name"],
            {
                "name": {"type": "string", "minLength": 1},
                "email": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "notes": {"type": ["string", "null"]},
            },
        ),
    ),
    "create_technician": ToolDefinition(
        "create_technician",
        "Add team technician",
        "Add an active technician to the tenant team after human approval.",
        "write",
        ("owner", "dispatcher"),
        object_schema(
            ["name", "email"],
            {
                "name": {"type": "string", "minLength": 1},
                "email": {"type": "string", "minLength": 3},
                "phone": {"type": ["string", "null"]},
                "timezone": {"type": "string", "minLength": 1},
            },
        ),
    ),
    "draft_job": ToolDefinition(
        "draft_job",
        "Create draft job",
        "Create a draft job for a customer after human approval.",
        "write",
        ("owner", "dispatcher"),
        object_schema(
            ["title"],
            {
                "customer_id": {"type": ["string", "null"], "format": "uuid"},
                "customer_name": {"type": ["string", "null"]},
                "title": {"type": "string", "minLength": 1},
                "description": {"type": ["string", "null"]},
            },
        ),
    ),
    "propose_schedule": ToolDefinition(
        "propose_schedule",
        "Schedule job",
        "Schedule a draft job after human approval and conflict checks.",
        "write",
        ("owner", "dispatcher"),
        object_schema(
            ["starts_at", "ends_at"],
            {
                "job_id": {"type": ["string", "null"], "format": "uuid"},
                "job_title": {"type": ["string", "null"]},
                "starts_at": {"type": "string", "format": "date-time"},
                "ends_at": {"type": "string", "format": "date-time"},
                "timezone": {"type": "string"},
                "technician_id": {"type": ["string", "null"]},
                "technician_name": {"type": ["string", "null"]},
            },
        ),
    ),
    "add_job_note": ToolDefinition(
        "add_job_note",
        "Add job note",
        "Append a note to a job after human approval.",
        "write",
        ("owner", "dispatcher", "technician"),
        object_schema(
            ["job_id", "body"],
            {
                "job_id": {"type": "string", "format": "uuid"},
                "body": {"type": "string", "minLength": 1},
            },
        ),
    ),
    "draft_invoice": ToolDefinition(
        "draft_invoice",
        "Draft invoice",
        "Create a draft invoice for a completed job after human approval.",
        "write",
        ("owner", "accountant"),
        object_schema(
            ["amount_cents"],
            {
                "job_id": {"type": ["string", "null"], "format": "uuid"},
                "job_title": {"type": ["string", "null"]},
                "currency": {"type": "string"},
                "description": {"type": "string"},
                "amount_cents": {"type": "integer", "minimum": 0},
            },
        ),
    ),
    "send_message": ToolDefinition(
        "send_message",
        "Queue outbound message",
        "Queue an externally visible message after human approval.",
        "external",
        ("owner", "dispatcher"),
        object_schema(
            ["recipient", "body"],
            {
                "recipient": {"type": "string", "minLength": 1},
                "body": {"type": "string", "minLength": 1},
                "channel": {"type": "string", "enum": ["sms", "email"]},
            },
        ),
    ),
}


def registry_payload(role: str) -> list[dict[str, Any]]:
    return [
        {
            "name": item.name,
            "title": item.title,
            "description": item.description,
            "risk": item.risk,
            "version": item.version,
            "timeout_seconds": item.timeout_seconds,
            "supports_dry_run": item.risk != "read",
            "input_schema": item.input_schema,
        }
        for item in TOOL_REGISTRY.values()
        if role in item.roles
    ]


def required(arguments: dict[str, Any], *names: str) -> None:
    missing = [name for name in names if arguments.get(name) in (None, "")]
    if missing:
        raise ToolInputError(f"Missing required field: {', '.join(missing)}")


def validate_arguments(definition: ToolDefinition, arguments: dict[str, Any]) -> None:
    properties = definition.input_schema["properties"]
    unexpected = set(arguments) - set(properties)
    if unexpected:
        raise ToolInputError(f"Unexpected field: {', '.join(sorted(unexpected))}")
    required(arguments, *definition.input_schema["required"])
    for name, value in arguments.items():
        if value is None:
            continue
        rule = properties[name]
        expected = rule.get("type")
        expected_types = expected if isinstance(expected, list) else [expected]
        if "string" in expected_types and not isinstance(value, str):
            raise ToolInputError(f"Field {name} must be a string")
        if "integer" in expected_types and not isinstance(value, int):
            raise ToolInputError(f"Field {name} must be an integer")
        if rule.get("format") == "uuid":
            try:
                uuid.UUID(str(value))
            except ValueError:
                raise ToolInputError(f"Field {name} must be a UUID") from None
        if isinstance(value, str) and len(value) < rule.get("minLength", 0):
            raise ToolInputError(f"Field {name} is too short")


def normalize_tool_arguments(definition: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
    """Keep only the explicitly allowlisted input fields for a model tool call."""
    allowed = definition.input_schema["properties"]
    return {name: value for name, value in arguments.items() if name in allowed}


def words(value: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", value.lower()) if len(word) > 2}


async def resolve_customer(session: AsyncSession, tenant_id: str, arguments: dict[str, Any]) -> Customer:
    if arguments.get("customer_id"):
        customer = await session.scalar(
            select(Customer).where(Customer.id == arguments["customer_id"], Customer.tenant_id == tenant_id)
        )
        if customer is None:
            raise ToolInputError("Customer not found")
        return customer
    name = str(arguments.get("customer_name") or "").strip()
    if not name:
        raise ToolInputError("Provide customer_id or customer_name")
    matches = list(
        await session.scalars(
            select(Customer).where(Customer.tenant_id == tenant_id, Customer.name.ilike(name)).limit(2)
        )
    )
    if not matches:
        raise ToolInputError(f'No customer named "{name}" was found')
    if len(matches) > 1:
        raise ToolInputError(f'More than one customer is named "{name}"; use the customer ID')
    return matches[0]


async def resolve_job(session: AsyncSession, tenant_id: str, arguments: dict[str, Any]) -> Job:
    if arguments.get("job_id"):
        job = await session.scalar(select(Job).where(Job.id == arguments["job_id"], Job.tenant_id == tenant_id))
        if job is None:
            raise ToolInputError("Job not found")
        return job
    title = str(arguments.get("job_title") or "").strip()
    if not title:
        raise ToolInputError("Provide job_id or job_title")
    matches = list(
        await session.scalars(select(Job).where(Job.tenant_id == tenant_id, Job.title.ilike(title)).limit(2))
    )
    if not matches:
        raise ToolInputError(f'No job titled "{title}" was found')
    if len(matches) > 1:
        raise ToolInputError(f'More than one job is titled "{title}"; use the job ID')
    return matches[0]


async def resolve_technician_reference(
    session: AsyncSession, tenant_id: str, arguments: dict[str, Any]
) -> Technician | None:
    if arguments.get("technician_id"):
        technician = await session.scalar(
            select(Technician).where(
                Technician.id == arguments["technician_id"],
                Technician.tenant_id == tenant_id,
                Technician.is_active.is_(True),
            )
        )
        if technician is None:
            raise ToolInputError("Active technician not found")
        return technician
    name = str(arguments.get("technician_name") or "").strip()
    if not name:
        return None
    matches = list(
        await session.scalars(
            select(Technician)
            .where(
                Technician.tenant_id == tenant_id,
                Technician.name.ilike(name),
                Technician.is_active.is_(True),
            )
            .limit(2)
        )
    )
    if not matches:
        raise ToolInputError(f'No active technician named "{name}" was found')
    if len(matches) > 1:
        raise ToolInputError(f'More than one technician is named "{name}"; use the technician ID')
    return matches[0]


async def search_knowledge(
    session: AsyncSession, tenant_id: str, role: str, query: str, limit: int = 5
) -> list[dict[str, Any]]:
    query_words = words(query)
    rows = await session.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeDocument.status == "indexed")
    )
    ranked: list[tuple[float, KnowledgeChunk, KnowledgeDocument]] = []
    for chunk, document in rows:
        if document.access_roles and role not in document.access_roles:
            continue
        overlap = query_words & (words(chunk.content) | words(document.title))
        if not overlap:
            continue
        score = len(overlap) / max(1, len(query_words))
        ranked.append((score, chunk, document))
    ranked.sort(key=lambda row: (-row[0], row[1].ordinal))
    return [
        {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "excerpt": chunk.content[:500],
            "source_uri": document.source_uri,
            "score": round(score, 4),
        }
        for score, chunk, document in ranked[:limit]
    ]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    context: TenantContext,
    request: Request,
    idempotency_key: str,
) -> dict[str, Any]:
    definition = TOOL_REGISTRY.get(name)
    if definition is None:
        raise ToolInputError("Unknown tool")
    if context.role not in definition.roles:
        raise HTTPException(status_code=403, detail="Role does not permit this tool")
    validate_arguments(definition, arguments)

    if name == "find_customer":
        required(arguments, "query")
        query = str(arguments["query"]).strip()
        customers = await session.scalars(
            select(Customer)
            .where(
                Customer.tenant_id == context.tenant_id,
                or_(Customer.name.ilike(f"%{query}%"), Customer.email.ilike(f"%{query}%")),
            )
            .order_by(Customer.name)
            .limit(10)
        )
        items = [{"id": item.id, "name": item.name, "email": item.email, "phone": item.phone} for item in customers]
        return {
            "summary": f"Found {len(items)} customer{'s' if len(items) != 1 else ''}.",
            "data": {"customers": items},
            "links": [{"label": item["name"], "resource": "customer", "id": item["id"]} for item in items],
            "citations": [],
        }

    if name == "fetch_job_history":
        required(arguments, "job_id")
        job = await session.scalar(select(Job).where(Job.id == arguments["job_id"], Job.tenant_id == context.tenant_id))
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        history = await session.scalars(
            select(JobStatusHistory)
            .where(JobStatusHistory.job_id == job.id, JobStatusHistory.tenant_id == context.tenant_id)
            .order_by(JobStatusHistory.occurred_at)
        )
        items = [
            {
                "from": item.from_status,
                "to": item.to_status,
                "occurred_at": item.occurred_at.isoformat(),
            }
            for item in history
        ]
        return {
            "summary": f"{job.title} is {job.status} with {len(items)} recorded transitions.",
            "data": {"job": JobRead.model_validate(job).model_dump(mode="json"), "history": items},
            "links": [{"label": job.title, "resource": "job", "id": job.id}],
            "citations": [],
        }

    if name == "check_availability":
        technicians = await session.scalars(
            select(Technician).where(Technician.tenant_id == context.tenant_id, Technician.is_active.is_(True))
        )
        items = []
        for technician in technicians:
            windows = await session.scalars(
                select(AvailabilityWindow)
                .where(AvailabilityWindow.technician_id == technician.id)
                .order_by(AvailabilityWindow.weekday, AvailabilityWindow.start_minute)
            )
            items.append(
                {
                    "id": technician.id,
                    "name": technician.name,
                    "timezone": technician.timezone,
                    "windows": [
                        {
                            "weekday": window.weekday,
                            "start_minute": window.start_minute,
                            "end_minute": window.end_minute,
                        }
                        for window in windows
                    ],
                }
            )
        return {
            "summary": f"Found {len(items)} active technicians with configured working windows.",
            "data": {"technicians": items},
            "links": [],
            "citations": [],
        }

    if name == "retrieve_invoice":
        required(arguments, "invoice_id")
        invoice = await session.scalar(
            select(Invoice)
            .options(selectinload(Invoice.lines))
            .where(Invoice.id == arguments["invoice_id"], Invoice.tenant_id == context.tenant_id)
        )
        if invoice is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        return {
            "summary": f"Invoice {invoice.number} is {invoice.status}; total {invoice.total_cents / 100:.2f} {invoice.currency}.",
            "data": {"invoice": InvoiceRead.model_validate(invoice).model_dump(mode="json")},
            "links": [{"label": invoice.number, "resource": "invoice", "id": invoice.id}],
            "citations": [],
        }

    if name == "search_knowledge":
        required(arguments, "query")
        results = await search_knowledge(session, context.tenant_id, context.role, str(arguments["query"]))
        citations = [
            {
                "id": result["chunk_id"],
                "source_type": "knowledge",
                "source_id": result["document_id"],
                "title": result["title"],
                "excerpt": result["excerpt"],
                "uri": result["source_uri"],
            }
            for result in results
        ]
        return {
            "summary": (
                "I found relevant authorized knowledge."
                if results
                else "No authorized knowledge matched that question."
            ),
            "data": {"results": results},
            "links": [],
            "citations": citations,
        }

    if name == "create_customer":
        required(arguments, "name")
        try:
            payload = CustomerCreate(
                name=arguments["name"],
                email=arguments.get("email"),
                phone=arguments.get("phone"),
                notes=arguments.get("notes"),
            )
        except ValueError as exc:
            raise ToolInputError(str(exc)) from None
        customer = await create_customer_record(
            payload=payload,
            request=request,
            context=context,
            session=session,
        )
        return {
            "summary": f"Added customer “{customer.name}”.",
            "data": {"customer": CustomerRead.model_validate(customer).model_dump(mode="json")},
            "links": [{"label": customer.name, "resource": "customer", "id": customer.id}],
            "citations": [],
        }

    if name == "create_technician":
        required(arguments, "name", "email")
        try:
            payload = TechnicianCreate(
                name=arguments["name"],
                email=arguments["email"],
                phone=arguments.get("phone"),
                timezone=arguments.get("timezone") or "UTC",
            )
        except ValueError as exc:
            raise ToolInputError(str(exc)) from None
        technician = await create_technician_record(
            payload=payload,
            request=request,
            context=context,
            session=session,
        )
        return {
            "summary": f"Added technician “{technician.name}” to the team.",
            "data": {"technician": TechnicianRead.model_validate(technician).model_dump(mode="json")},
            "links": [{"label": technician.name, "resource": "technician", "id": technician.id}],
            "citations": [],
        }

    if name == "draft_job":
        required(arguments, "title")
        customer = await resolve_customer(session, context.tenant_id, arguments)
        job = await create_job(
            JobCreate(
                customer_id=customer.id,
                title=arguments["title"],
                description=arguments.get("description"),
            ),
            request,
            idempotency_key,
            context,
            session,
        )
        return {
            "summary": f"Created draft job “{job.title}”.",
            "data": {"job": JobRead.model_validate(job).model_dump(mode="json")},
            "links": [{"label": job.title, "resource": "job", "id": job.id}],
            "citations": [],
        }

    if name == "add_job_note":
        required(arguments, "job_id", "body")
        note = await add_job_note(arguments["job_id"], JobNoteCreate(body=arguments["body"]), request, context, session)
        return {
            "summary": "Added the approved note to the job.",
            "data": {"note_id": note.id, "body": note.body},
            "links": [{"label": "Open job", "resource": "job", "id": arguments["job_id"]}],
            "citations": [],
        }

    if name == "propose_schedule":
        required(arguments, "starts_at", "ends_at")
        job = await resolve_job(session, context.tenant_id, arguments)
        technician = await resolve_technician_reference(session, context.tenant_id, arguments)
        appointment = await schedule_job(
            job.id,
            ScheduleJob(
                starts_at=arguments["starts_at"],
                ends_at=arguments["ends_at"],
                timezone=arguments.get("timezone") or "UTC",
                technician_id=technician.id if technician else None,
                expected_version=job.version,
            ),
            request,
            idempotency_key,
            context,
            session,
        )
        return {
            "summary": f"Scheduled the job for {appointment.starts_at.isoformat()}.",
            "data": {"appointment_id": appointment.id, "starts_at": appointment.starts_at.isoformat()},
            "links": [{"label": "Open schedule", "resource": "appointment", "id": appointment.id}],
            "citations": [],
        }

    if name == "draft_invoice":
        required(arguments, "amount_cents")
        job = await resolve_job(session, context.tenant_id, arguments)
        invoice = await draft_invoice(
            job.id,
            DraftInvoice(
                expected_job_version=job.version,
                currency=arguments.get("currency") or "USD",
                lines=[
                    {
                        "description": arguments.get("description") or "Service",
                        "quantity": 1,
                        "unit_price_cents": arguments["amount_cents"],
                    }
                ],
            ),
            request,
            idempotency_key,
            context,
            session,
        )
        return {
            "summary": f"Created draft invoice {invoice.number}.",
            "data": {"invoice": InvoiceRead.model_validate(invoice).model_dump(mode="json")},
            "links": [{"label": invoice.number, "resource": "invoice", "id": invoice.id}],
            "citations": [],
        }

    if name == "send_message":
        required(arguments, "recipient", "body")
        message = OutboundMessage(
            tenant_id=context.tenant_id,
            requested_by_user_id=context.user.id,
            channel=arguments.get("channel") or "sms",
            recipient=arguments["recipient"],
            body=arguments["body"],
            status="queued",
            idempotency_key=idempotency_key,
        )
        session.add(message)
        await session.flush()
        session.add(
            OutboxEvent(
                tenant_id=context.tenant_id,
                event_type="message.requested",
                aggregate_type="outbound_message",
                aggregate_id=message.id,
                payload={"channel": message.channel, "recipient": message.recipient},
                occurred_at=datetime.now(timezone.utc),
                correlation_id=request.state.correlation_id,
            )
        )
        await session.commit()
        return {
            "summary": f"Queued the approved {message.channel} message for delivery.",
            "data": {"message_id": message.id, "status": message.status, "channel": message.channel},
            "links": [],
            "citations": [],
        }

    raise ToolInputError("Tool is not executable")
