import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.omni_api.models import (
    AuditEvent,
    AutomationApproval,
    AutomationDeadLetter,
    AutomationDefinition,
    AutomationRun,
    AutomationRunEvent,
    AutomationTriggerEvent,
    AutomationVersion,
    Customer,
    Invoice,
    InvoiceLine,
    Job,
    JobNote,
    OutboundMessage,
    OutboxEvent,
)

AUTOMATION_TEMPLATES: dict[str, dict[str, Any]] = {
    "lead_intake": {
        "name": "Lead intake",
        "description": "Turn a newly qualified lead into a draft job for review.",
        "trigger_type": "lead.qualified",
        "conditions": [{"field": "customer_id", "operator": "exists", "value": None}],
        "steps": [
            {"kind": "extract", "operation": "extract", "config": {"schema": "job_request"}},
            {
                "kind": "action",
                "operation": "create_job",
                "config": {
                    "customer_id": "{{payload.customer_id}}",
                    "title": "{{payload.title}}",
                    "description": "{{payload.description}}",
                },
            },
        ],
        "approval_rule": {"mode": "writes"},
    },
    "appointment_reminder": {
        "name": "Appointment reminder",
        "description": "Send a reminder before a scheduled appointment.",
        "trigger_type": "appointment.upcoming",
        "conditions": [{"field": "recipient", "operator": "exists", "value": None}],
        "steps": [
            {
                "kind": "action",
                "operation": "send_message",
                "config": {
                    "channel": "sms",
                    "recipient": "{{payload.recipient}}",
                    "body": "Reminder: your appointment is scheduled for {{payload.starts_at}}.",
                },
            }
        ],
        "approval_rule": {"mode": "external"},
    },
    "missed_call_follow_up": {
        "name": "Missed-call follow-up",
        "description": "Acknowledge a missed call and invite the caller to continue by message.",
        "trigger_type": "call.missed",
        "conditions": [{"field": "caller", "operator": "exists", "value": None}],
        "steps": [
            {
                "kind": "action",
                "operation": "send_message",
                "config": {
                    "channel": "sms",
                    "recipient": "{{payload.caller}}",
                    "body": "Sorry we missed your call. Reply here and our team will follow up.",
                },
            }
        ],
        "approval_rule": {"mode": "external"},
    },
    "job_completion_summary": {
        "name": "Job completion summary",
        "description": "Generate and attach a concise completion summary to the job.",
        "trigger_type": "job.completed",
        "conditions": [{"field": "job_id", "operator": "exists", "value": None}],
        "steps": [
            {"kind": "ai", "operation": "summarize", "config": {"source": "description"}},
            {
                "kind": "action",
                "operation": "add_job_note",
                "config": {"job_id": "{{payload.job_id}}", "body": "Completion summary: {{payload.summary}}"},
            },
        ],
        "approval_rule": {"mode": "writes"},
    },
    "invoice_draft": {
        "name": "Invoice draft",
        "description": "Prepare a draft invoice after job completion.",
        "trigger_type": "job.completed",
        "conditions": [{"field": "job_id", "operator": "exists", "value": None}],
        "steps": [
            {
                "kind": "action",
                "operation": "draft_invoice",
                "config": {
                    "job_id": "{{payload.job_id}}",
                    "description": "{{payload.invoice_description}}",
                    "amount_cents": "{{payload.amount_cents}}",
                },
            }
        ],
        "approval_rule": {"mode": "writes"},
    },
    "overdue_invoice_follow_up": {
        "name": "Overdue-invoice follow-up",
        "description": "Send a measured payment reminder for an overdue invoice.",
        "trigger_type": "invoice.overdue",
        "conditions": [{"field": "recipient", "operator": "exists", "value": None}],
        "steps": [
            {
                "kind": "action",
                "operation": "send_message",
                "config": {
                    "channel": "email",
                    "recipient": "{{payload.recipient}}",
                    "body": "Invoice {{payload.invoice_number}} is overdue. Please contact us if you need help.",
                },
            }
        ],
        "approval_rule": {"mode": "external"},
    },
    "review_escalation": {
        "name": "Review escalation",
        "description": "Escalate an AI item that remains pending human review.",
        "trigger_type": "review.overdue",
        "conditions": [{"field": "recipient", "operator": "exists", "value": None}],
        "steps": [
            {
                "kind": "action",
                "operation": "send_message",
                "config": {
                    "channel": "email",
                    "recipient": "{{payload.recipient}}",
                    "body": "Review {{payload.review_id}} needs attention.",
                },
            }
        ],
        "approval_rule": {"mode": "external"},
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_path(payload: dict, path: str) -> Any:
    value: Any = payload
    for part in path.removeprefix("payload.").split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def render(value: Any, payload: dict) -> Any:
    if isinstance(value, dict):
        return {key: render(item, payload) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, payload) for item in value]
    if not isinstance(value, str):
        return value
    exact = re.fullmatch(r"\{\{payload\.([^}]+)}}", value)
    if exact:
        return get_path(payload, exact.group(1))
    return re.sub(
        r"\{\{payload\.([^}]+)}}",
        lambda match: str(get_path(payload, match.group(1)) or ""),
        value,
    )


def conditions_match(conditions: list[dict], payload: dict) -> tuple[bool, list[str]]:
    explanations: list[str] = []
    for condition in conditions:
        actual = get_path(payload, condition["field"])
        expected = condition.get("value")
        operator = condition["operator"]
        if operator == "equals":
            matched = actual == expected
        elif operator == "not_equals":
            matched = actual != expected
        elif operator == "contains":
            matched = expected is not None and isinstance(actual, (str, list, dict)) and expected in actual
        elif operator == "exists":
            matched = actual is not None and actual != ""
        elif operator in {"gt", "gte", "lt", "lte"}:
            matched = (
                actual is not None
                and expected is not None
                and {
                    "gt": lambda: actual > expected,
                    "gte": lambda: actual >= expected,
                    "lt": lambda: actual < expected,
                    "lte": lambda: actual <= expected,
                }[operator]()
            )
        else:
            matched = False
        explanations.append(f"{condition['field']} {operator}: {'matched' if matched else 'did not match'}")
        if not matched:
            return False, explanations
    return True, explanations


async def append_run_event(session: AsyncSession, run: AutomationRun, event_type: str, payload: dict) -> None:
    sequence = (
        await session.scalar(select(func.max(AutomationRunEvent.sequence)).where(AutomationRunEvent.run_id == run.id))
        or 0
    ) + 1
    session.add(
        AutomationRunEvent(
            tenant_id=run.tenant_id,
            run_id=run.id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
            occurred_at=utcnow(),
        )
    )


async def create_runs_for_trigger(session: AsyncSession, event: AutomationTriggerEvent) -> list[AutomationRun]:
    definitions = list(
        await session.scalars(
            select(AutomationDefinition).where(
                AutomationDefinition.tenant_id == event.tenant_id,
                AutomationDefinition.status == "active",
            )
        )
    )
    runs: list[AutomationRun] = []
    for definition in definitions:
        version = await session.scalar(
            select(AutomationVersion).where(
                AutomationVersion.definition_id == definition.id,
                AutomationVersion.version == definition.current_version,
                AutomationVersion.trigger_type == event.trigger_type,
            )
        )
        if version is None:
            continue
        recent = await session.scalar(
            select(func.count(AutomationRun.id)).where(
                AutomationRun.definition_id == definition.id,
                AutomationRun.created_at >= utcnow() - timedelta(hours=1),
            )
        )
        status = "rate_limited" if (recent or 0) >= version.rate_limit_per_hour else "queued"
        run = AutomationRun(
            tenant_id=event.tenant_id,
            definition_id=definition.id,
            version_id=version.id,
            trigger_event_id=event.id,
            status=status,
            mode=definition.mode,
            reason=f"Matched active automation v{version.version} for {event.trigger_type}",
            input=event.payload,
            max_attempts=version.max_attempts,
        )
        session.add(run)
        await session.flush()
        await append_run_event(
            session,
            run,
            "run_created" if status == "queued" else "rate_limited",
            {"trigger_type": event.trigger_type, "mode": definition.mode},
        )
        runs.append(run)
    return runs


def approval_required(rule: dict, actions: list[dict]) -> bool:
    mode = rule.get("mode", "writes")
    if mode == "always":
        return True
    if mode == "never":
        return False
    if mode == "external":
        return any(action["operation"] == "send_message" for action in actions)
    return bool(actions)


async def planned_actions(version: AutomationVersion, payload: dict) -> tuple[list[dict], dict]:
    working = dict(payload)
    derived: dict[str, Any] = {}
    actions: list[dict] = []
    for step in version.steps:
        if step["kind"] == "ai" and step["operation"] == "summarize":
            source = str(working.get(step.get("config", {}).get("source", "description"), "Job completed"))
            derived["summary"] = " ".join(source.split())[:500] or "Job completed"
            working["summary"] = derived["summary"]
        elif step["kind"] == "extract":
            derived["extraction"] = {"schema": step.get("config", {}).get("schema", "generic"), "status": "completed"}
        elif step["kind"] == "action":
            actions.append({"operation": step["operation"], "arguments": render(step.get("config", {}), working)})
    return actions, derived


def record_resource_event(
    session: AsyncSession, run: AutomationRun, action: str, resource_type: str, resource_id: str, payload: dict
) -> None:
    correlation_id = f"automation:{run.id}"
    session.add(
        AuditEvent(
            tenant_id=run.tenant_id,
            actor_user_id=None,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload={**payload, "automation_run_id": run.id},
            occurred_at=utcnow(),
            correlation_id=correlation_id,
        )
    )
    session.add(
        OutboxEvent(
            tenant_id=run.tenant_id,
            event_type=action,
            aggregate_type=resource_type,
            aggregate_id=resource_id,
            payload={**payload, "automation_run_id": run.id},
            occurred_at=utcnow(),
            correlation_id=correlation_id,
        )
    )


async def apply_action(session: AsyncSession, run: AutomationRun, action: dict) -> dict:
    operation = action["operation"]
    arguments = action.get("arguments", {})
    if operation == "create_job":
        customer = await session.scalar(
            select(Customer).where(Customer.id == arguments.get("customer_id"), Customer.tenant_id == run.tenant_id)
        )
        if customer is None:
            raise ValueError("Customer not found for automated job")
        job = Job(
            tenant_id=run.tenant_id,
            customer_id=customer.id,
            title=str(arguments.get("title") or "New lead"),
            description=arguments.get("description"),
            status="draft",
        )
        session.add(job)
        await session.flush()
        record_resource_event(session, run, "job.created", "job", job.id, {"customer_id": customer.id})
        return {"type": "job", "id": job.id, "operation": "created", "reversible": True}
    if operation == "add_job_note":
        job = await session.scalar(select(Job).where(Job.id == arguments.get("job_id"), Job.tenant_id == run.tenant_id))
        if job is None:
            raise ValueError("Job not found for automated note")
        note = JobNote(
            tenant_id=run.tenant_id, job_id=job.id, author_user_id=None, body=str(arguments.get("body") or "")
        )
        session.add(note)
        await session.flush()
        record_resource_event(session, run, "job.note_added", "job_note", note.id, {"job_id": job.id})
        return {"type": "job_note", "id": note.id, "operation": "created", "reversible": True}
    if operation == "draft_invoice":
        job = await session.scalar(select(Job).where(Job.id == arguments.get("job_id"), Job.tenant_id == run.tenant_id))
        if job is None:
            raise ValueError("Job not found for automated invoice")
        existing = await session.scalar(select(Invoice).where(Invoice.job_id == job.id))
        if existing is not None:
            return {"type": "invoice", "id": existing.id, "operation": "deduplicated", "reversible": False}
        amount = int(arguments.get("amount_cents") or 0)
        invoice = Invoice(
            tenant_id=run.tenant_id,
            customer_id=job.customer_id,
            job_id=job.id,
            number=f"AUTO-{run.id[:8].upper()}",
            status="draft",
            currency="USD",
            subtotal_cents=amount,
            total_cents=amount,
        )
        session.add(invoice)
        await session.flush()
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                description=str(arguments.get("description") or "Completed work"),
                quantity=1,
                unit_price_cents=amount,
                amount_cents=amount,
            )
        )
        record_resource_event(session, run, "invoice.drafted", "invoice", invoice.id, {"job_id": job.id})
        return {"type": "invoice", "id": invoice.id, "operation": "created", "reversible": True}
    if operation == "send_message":
        recipient = str(arguments.get("recipient") or "").strip()
        body = str(arguments.get("body") or "").strip()
        if not recipient or not body:
            raise ValueError("Recipient and body are required for automated messages")
        message = OutboundMessage(
            tenant_id=run.tenant_id,
            requested_by_user_id=None,
            channel=str(arguments.get("channel") or "sms"),
            recipient=recipient,
            body=body,
            status="queued",
            idempotency_key=f"automation:{run.id}:{len(run.changed_resources)}",
        )
        session.add(message)
        await session.flush()
        record_resource_event(
            session, run, "message.queued", "outbound_message", message.id, {"channel": message.channel}
        )
        return {"type": "outbound_message", "id": message.id, "operation": "queued", "reversible": True}
    raise ValueError(f"Unsupported automation action: {operation}")


async def execute_run(session: AsyncSession, run_id: str, approved_actions: list[dict] | None = None) -> AutomationRun:
    run = await session.scalar(select(AutomationRun).where(AutomationRun.id == run_id).with_for_update())
    if run is None:
        raise ValueError("Automation run not found")
    definition = await session.get(AutomationDefinition, run.definition_id)
    version = await session.get(AutomationVersion, run.version_id)
    if definition is None or version is None:
        raise ValueError("Automation definition version not found")
    if (
        definition.status != "active"
        and not run.input.get("_test_mode")
        and run.status not in {"waiting_approval", "retrying"}
    ):
        run.status = "cancelled"
        run.reason = f"Automation is {definition.status}"
        run.completed_at = utcnow()
        await append_run_event(session, run, "run_cancelled", {"definition_status": definition.status})
        return run
    if run.status in {"succeeded", "shadowed", "skipped", "rejected", "compensated", "cancelled"}:
        return run
    run.status = "running"
    run.started_at = run.started_at or utcnow()
    run.attempt += 1
    await append_run_event(session, run, "run_started", {"attempt": run.attempt})
    matched, explanations = conditions_match(version.conditions, run.input)
    if not matched:
        run.status = "skipped"
        run.reason = "; ".join(explanations)
        run.completed_at = utcnow()
        await append_run_event(session, run, "conditions_not_matched", {"conditions": explanations})
        return run
    actions, derived = await planned_actions(version, run.input)
    await append_run_event(session, run, "conditions_matched", {"conditions": explanations})
    if run.mode == "shadow":
        run.status = "shadowed"
        run.output = {"planned_actions": actions, "derived": derived, "side_effects": False}
        run.reason = "Shadow mode evaluated successfully; no records were changed"
        run.completed_at = utcnow()
        await append_run_event(session, run, "shadow_completed", run.output)
        return run
    if approved_actions is None and approval_required(version.approval_rule, actions):
        approval = await session.scalar(select(AutomationApproval).where(AutomationApproval.run_id == run.id))
        if approval is None:
            approval = AutomationApproval(
                tenant_id=run.tenant_id,
                run_id=run.id,
                status="pending",
                proposed_actions=actions,
            )
            session.add(approval)
        run.status = "waiting_approval"
        run.output = {"planned_actions": actions, "derived": derived}
        run.reason = "Policy requires human approval before side effects"
        await append_run_event(session, run, "approval_requested", {"actions": actions})
        return run
    selected_actions = approved_actions if approved_actions is not None else actions
    changed: list[dict] = list(run.changed_resources)
    try:
        completed_indexes = {item.get("action_index") for item in changed}
        for action_index, action in enumerate(selected_actions):
            if action_index in completed_indexes:
                continue
            resource = await apply_action(session, run, action)
            resource["action_index"] = action_index
            changed.append(resource)
            run.changed_resources = changed
            await append_run_event(session, run, "action_completed", {"action": action, "resource": resource})
    except Exception as exc:
        run.error_code = "action_failed"
        run.error_message = str(exc)
        if run.attempt >= run.max_attempts:
            run.status = "dead_letter"
            run.completed_at = utcnow()
            session.add(
                AutomationDeadLetter(
                    tenant_id=run.tenant_id,
                    run_id=run.id,
                    reason=str(exc),
                    payload={"actions": selected_actions, "attempt": run.attempt},
                )
            )
            await append_run_event(session, run, "dead_lettered", {"error": str(exc)})
        else:
            run.status = "retrying"
            run.next_retry_at = utcnow() + timedelta(seconds=min(300, 2**run.attempt))
            await append_run_event(
                session, run, "retry_scheduled", {"error": str(exc), "next_retry_at": run.next_retry_at.isoformat()}
            )
        return run
    run.changed_resources = changed
    run.output = {"derived": derived, "actions_completed": len(selected_actions)}
    run.status = "succeeded"
    run.reason = f"Completed {len(selected_actions)} action(s)"
    run.completed_at = utcnow()
    run.next_retry_at = None
    await append_run_event(session, run, "run_succeeded", run.output)
    return run


async def compensate_run(session: AsyncSession, run: AutomationRun) -> list[dict]:
    compensated: list[dict] = []
    for resource in reversed(run.changed_resources):
        if not resource.get("reversible"):
            continue
        resource_type = resource.get("type")
        resource_id = resource.get("id")
        if resource_type == "outbound_message":
            message = await session.get(OutboundMessage, resource_id)
            if message is not None and message.status == "queued":
                message.status = "cancelled"
                compensated.append({"type": resource_type, "id": resource_id, "result": "cancelled"})
        elif resource_type == "invoice":
            invoice = await session.get(Invoice, resource_id)
            if invoice is not None and invoice.status == "draft":
                invoice.status = "void"
                invoice.voided_at = utcnow()
                compensated.append({"type": resource_type, "id": resource_id, "result": "voided"})
        elif resource_type == "job":
            job = await session.get(Job, resource_id)
            if job is not None and job.status == "draft":
                job.status = "cancelled"
                compensated.append({"type": resource_type, "id": resource_id, "result": "cancelled"})
        elif resource_type == "job_note":
            note = await session.get(JobNote, resource_id)
            if note is not None:
                await session.delete(note)
                compensated.append({"type": resource_type, "id": resource_id, "result": "removed"})
    run.status = "compensated"
    run.completed_at = utcnow()
    run.output = {**run.output, "compensated_resources": compensated}
    await append_run_event(session, run, "run_compensated", {"resources": compensated})
    return compensated
