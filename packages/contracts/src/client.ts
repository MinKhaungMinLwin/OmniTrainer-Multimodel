import type { components } from "./schema";

export type Tenant = components["schemas"]["TenantRead"];
export type User = components["schemas"]["UserRead"];
export type Customer = components["schemas"]["CustomerRead"];
export type CustomerCreate = components["schemas"]["CustomerCreate"];
export type CustomerList = components["schemas"]["CustomerList"];
export type Job = components["schemas"]["JobRead"];
export type JobCreate = components["schemas"]["JobCreate"];
export type JobList = components["schemas"]["JobList"];
export type ScheduleJob = components["schemas"]["ScheduleJob"];
export type Appointment = components["schemas"]["AppointmentRead"];
export type Invoice = components["schemas"]["InvoiceRead"];
export type InvoiceList = components["schemas"]["InvoiceList"];
export type DraftInvoice = components["schemas"]["DraftInvoice"];
export type CustomerDetail = components["schemas"]["CustomerDetail"];
export type CustomerUpdate = components["schemas"]["CustomerUpdate"];
export type ContactCreate = components["schemas"]["ContactCreate"];
export type LocationCreate = components["schemas"]["LocationCreate"];
export type Technician = components["schemas"]["TechnicianRead"];
export type TechnicianCreate = components["schemas"]["TechnicianCreate"];
export type Availability = components["schemas"]["AvailabilityRead"];
export type JobNote = components["schemas"]["JobNoteRead"];
export type JobStatus = components["schemas"]["JobStatusRead"];
export type Attachment = components["schemas"]["AttachmentRead"];
export type Payment = components["schemas"]["PaymentRead"];
export type PaymentCreate = components["schemas"]["PaymentCreate"];
export type InvoiceLineCreate = components["schemas"]["InvoiceLineCreate"];
export type Conversation = components["schemas"]["ConversationRead"];
export type ConversationMessage = components["schemas"]["MessageRead"];
export type AgentRun = components["schemas"]["RunRead"];
export type AgentEvent = components["schemas"]["EventRead"];
export type AgentRunDetail = components["schemas"]["RunDetail"];
export type ToolInvocation = components["schemas"]["ToolInvocationRead"];
export type Approval = components["schemas"]["ApprovalRead"];
export type ApprovalDecision =
  components["schemas"]["services__api__omni_api__ai_schemas__ApprovalDecision"];
export type AgentFeedback = components["schemas"]["FeedbackRead"];
export type FeedbackCreate = components["schemas"]["FeedbackCreate"];
export type KnowledgeDocument = components["schemas"]["KnowledgeDocumentRead"];
export type KnowledgeDocumentCreate =
  components["schemas"]["KnowledgeDocumentCreate"];
export type KnowledgeSearchResult =
  components["schemas"]["KnowledgeSearchResult"];
export type ExtractionRun = components["schemas"]["ExtractionRead"];
export type ExtractionCreate = components["schemas"]["ExtractionCreate"];
export type ExtractionReview = components["schemas"]["ExtractionReview"];
export type AutomationTemplate = components["schemas"]["TemplateRead"];
export type AutomationDefinition =
  components["schemas"]["AutomationDefinitionRead"];
export type AutomationDefinitionCreate =
  components["schemas"]["AutomationDefinitionCreate"];
export type AutomationDefinitionUpdate =
  components["schemas"]["AutomationDefinitionUpdate"];
export type AutomationRun = components["schemas"]["AutomationRunRead"];
export type AutomationMetrics = components["schemas"]["AutomationMetricsRead"];
export type AutomationTrigger = components["schemas"]["TriggerEventCreate"];
export type AutomationApprovalDecision =
  components["schemas"]["services__api__omni_api__automation_schemas__ApprovalDecision"];
export type VoiceFlowConfig = components["schemas"]["VoiceFlowConfigRead"];
export type VoiceFlowConfigUpdate =
  components["schemas"]["VoiceFlowConfigUpdate"];
export type VoiceCall = components["schemas"]["VoiceCallRead"];
export type VoiceReviewDecision = components["schemas"]["VoiceReviewDecision"];
export type VoiceSimulation = components["schemas"]["VoiceSimulationCreate"];
export type VoiceMetrics = components["schemas"]["VoiceMetricsRead"];
export type IntelligenceDashboard =
  components["schemas"]["IntelligenceDashboard"];
export type CallIntelligenceDetail =
  components["schemas"]["CallIntelligenceDetail"];
export type IntelligenceSearchResult =
  components["schemas"]["IntelligenceSearchResult"];
export type IntelligenceReview = components["schemas"]["IntelligenceReview"];
export type TranscriptCorrection =
  components["schemas"]["TranscriptCorrection"];
export type ReconciliationCreate =
  components["schemas"]["ReconciliationCreate"];
export type Reconciliation = components["schemas"]["ReconciliationRead"];
export type MetricDefinition = components["schemas"]["MetricDefinition"];

export interface AiToolDefinition {
  name: string;
  title: string;
  description: string;
  risk: "read" | "write" | "external";
  version: string;
  timeout_seconds: number;
  input_schema: Record<string, unknown>;
}

export interface ApiClientOptions {
  baseUrl: string;
  getToken: () => string | null | Promise<string | null>;
  getTenantId: () => string | null | Promise<string | null>;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export class OmniApiClient {
  constructor(private readonly options: ApiClientOptions) {}

  health(): Promise<{ status: string }> {
    return this.request("/health/ready", {}, false);
  }

  async createDevelopmentToken(email: string): Promise<string> {
    const response = await this.request<{ access_token: string }>(
      "/api/v1/dev/token",
      { method: "POST", body: JSON.stringify({ email }) },
      false,
    );
    return response.access_token;
  }

  me(): Promise<User> {
    return this.request("/api/v1/me");
  }

  tenants(): Promise<Tenant[]> {
    return this.request("/api/v1/tenants");
  }

  customers(query = ""): Promise<CustomerList> {
    const suffix = query ? `?query=${encodeURIComponent(query)}` : "";
    return this.request(`/api/v1/customers${suffix}`, {}, true, true);
  }

  createCustomer(customer: CustomerCreate): Promise<Customer> {
    return this.request(
      "/api/v1/customers",
      { method: "POST", body: JSON.stringify(customer) },
      true,
      true,
    );
  }

  customer(customerId: string): Promise<CustomerDetail> {
    return this.request(`/api/v1/customers/${customerId}`, {}, true, true);
  }

  updateCustomer(
    customerId: string,
    customer: CustomerUpdate,
  ): Promise<CustomerDetail> {
    return this.request(
      `/api/v1/customers/${customerId}`,
      { method: "PUT", body: JSON.stringify(customer) },
      true,
      true,
    );
  }

  addContact(
    customerId: string,
    contact: ContactCreate,
  ): Promise<CustomerDetail["contacts"][number]> {
    return this.request(
      `/api/v1/customers/${customerId}/contacts`,
      { method: "POST", body: JSON.stringify(contact) },
      true,
      true,
    );
  }

  addLocation(
    customerId: string,
    location: LocationCreate,
  ): Promise<CustomerDetail["locations"][number]> {
    return this.request(
      `/api/v1/customers/${customerId}/locations`,
      { method: "POST", body: JSON.stringify(location) },
      true,
      true,
    );
  }

  jobs(): Promise<JobList> {
    return this.request("/api/v1/jobs", {}, true, true);
  }

  createJob(job: JobCreate): Promise<Job> {
    return this.command("/api/v1/jobs", job);
  }

  scheduleJob(jobId: string, schedule: ScheduleJob): Promise<Appointment> {
    return this.command(`/api/v1/jobs/${jobId}/schedule`, schedule);
  }

  completeJob(jobId: string, expectedVersion: number): Promise<Job> {
    return this.command(`/api/v1/jobs/${jobId}/complete`, {
      expected_version: expectedVersion,
    });
  }

  appointments(): Promise<Appointment[]> {
    return this.request("/api/v1/appointments", {}, true, true);
  }

  rescheduleAppointment(
    appointmentId: string,
    schedule: components["schemas"]["RescheduleAppointment"],
  ): Promise<Appointment> {
    return this.request(
      `/api/v1/appointments/${appointmentId}/reschedule`,
      { method: "POST", body: JSON.stringify(schedule) },
      true,
      true,
    );
  }

  technicians(): Promise<Technician[]> {
    return this.request("/api/v1/technicians", {}, true, true);
  }

  createTechnician(technician: TechnicianCreate): Promise<Technician> {
    return this.request(
      "/api/v1/technicians",
      { method: "POST", body: JSON.stringify(technician) },
      true,
      true,
    );
  }

  technicianAvailability(technicianId: string): Promise<Availability[]> {
    return this.request(
      `/api/v1/technicians/${technicianId}/availability`,
      {},
      true,
      true,
    );
  }

  addTechnicianAvailability(
    technicianId: string,
    availability: components["schemas"]["AvailabilityCreate"],
  ): Promise<Availability> {
    return this.request(
      `/api/v1/technicians/${technicianId}/availability`,
      { method: "POST", body: JSON.stringify(availability) },
      true,
      true,
    );
  }

  jobNotes(jobId: string): Promise<JobNote[]> {
    return this.request(`/api/v1/jobs/${jobId}/notes`, {}, true, true);
  }

  addJobNote(jobId: string, body: string): Promise<JobNote> {
    return this.request(
      `/api/v1/jobs/${jobId}/notes`,
      { method: "POST", body: JSON.stringify({ body }) },
      true,
      true,
    );
  }

  jobHistory(jobId: string): Promise<JobStatus[]> {
    return this.request(`/api/v1/jobs/${jobId}/history`, {}, true, true);
  }

  jobAttachments(jobId: string): Promise<Attachment[]> {
    return this.request(`/api/v1/jobs/${jobId}/attachments`, {}, true, true);
  }

  uploadJobAttachment(
    jobId: string,
    file: Blob,
    filename: string,
  ): Promise<Attachment> {
    const form = new FormData();
    form.append("file", file, filename);
    return this.request(
      `/api/v1/jobs/${jobId}/attachments`,
      { method: "POST", body: form },
      true,
      true,
    );
  }

  invoices(): Promise<InvoiceList> {
    return this.request("/api/v1/invoices", {}, true, true);
  }

  draftInvoice(jobId: string, invoice: DraftInvoice): Promise<Invoice> {
    return this.command(`/api/v1/jobs/${jobId}/invoice`, invoice);
  }

  updateInvoiceLines(
    invoiceId: string,
    expectedVersion: number,
    lines: InvoiceLineCreate[],
  ): Promise<Invoice> {
    return this.request(
      `/api/v1/invoices/${invoiceId}/lines`,
      {
        method: "PUT",
        body: JSON.stringify({ expected_version: expectedVersion, lines }),
      },
      true,
      true,
    );
  }

  invoicePayments(invoiceId: string): Promise<Payment[]> {
    return this.request(
      `/api/v1/invoices/${invoiceId}/payments`,
      {},
      true,
      true,
    );
  }

  recordPayment(invoiceId: string, payment: PaymentCreate): Promise<Payment> {
    return this.command(`/api/v1/invoices/${invoiceId}/payments`, payment);
  }

  async invoicePdf(invoiceId: string): Promise<Blob> {
    return (
      await this.fetchResponse(
        `/api/v1/invoices/${invoiceId}/pdf`,
        {},
        true,
        true,
      )
    ).blob();
  }

  async attachmentContent(attachmentId: string): Promise<Blob> {
    return (
      await this.fetchResponse(
        `/api/v1/attachments/${attachmentId}/download`,
        {},
        true,
        true,
      )
    ).blob();
  }

  issueInvoice(invoiceId: string, expectedVersion: number): Promise<Invoice> {
    return this.command(`/api/v1/invoices/${invoiceId}/issue`, {
      expected_version: expectedVersion,
    });
  }

  voidInvoice(invoiceId: string, expectedVersion: number): Promise<Invoice> {
    return this.command(`/api/v1/invoices/${invoiceId}/void`, {
      expected_version: expectedVersion,
    });
  }

  aiTools(): Promise<AiToolDefinition[]> {
    return this.request("/api/v1/ai/tools", {}, true, true);
  }

  transcribeAudio(file: Blob): Promise<{ text: string; model: string }> {
    const form = new FormData();
    const extension = file.type.includes("ogg")
      ? "ogg"
      : file.type.includes("mp4")
        ? "m4a"
        : "webm";
    form.append("file", file, `recording.${extension}`);
    return this.request(
      "/api/v1/ai/audio/transcriptions",
      { method: "POST", body: form },
      true,
      true,
    );
  }

  async synthesizeSpeech(text: string, voice?: string): Promise<Blob> {
    return (
      await this.fetchResponse(
        "/api/v1/ai/audio/speech",
        {
          method: "POST",
          body: JSON.stringify({ text, voice: voice ?? null }),
        },
        true,
        true,
      )
    ).blob();
  }

  conversations(): Promise<Conversation[]> {
    return this.request("/api/v1/ai/conversations", {}, true, true);
  }

  createConversation(title = "New conversation"): Promise<Conversation> {
    return this.request(
      "/api/v1/ai/conversations",
      { method: "POST", body: JSON.stringify({ title }) },
      true,
      true,
    );
  }

  conversationMessages(conversationId: string): Promise<ConversationMessage[]> {
    return this.request(
      `/api/v1/ai/conversations/${conversationId}/messages`,
      {},
      true,
      true,
    );
  }

  createAgentRun(
    conversationId: string,
    content: string,
    parentRunId?: string,
  ): Promise<AgentRunDetail> {
    return this.command(`/api/v1/ai/conversations/${conversationId}/runs`, {
      content,
      parent_run_id: parentRunId,
    });
  }

  agentRun(runId: string): Promise<AgentRunDetail> {
    return this.request(`/api/v1/ai/runs/${runId}`, {}, true, true);
  }

  agentRunEvents(runId: string, after = 0): Promise<AgentEvent[]> {
    return this.request(
      `/api/v1/ai/runs/${runId}/events?after=${after}`,
      {},
      true,
      true,
    );
  }

  cancelAgentRun(runId: string): Promise<AgentRunDetail> {
    return this.request(
      `/api/v1/ai/runs/${runId}/cancel`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  regenerateAgentRun(runId: string): Promise<AgentRunDetail> {
    return this.command(`/api/v1/ai/runs/${runId}/regenerate`, {});
  }

  decideApproval(
    approvalId: string,
    decision: ApprovalDecision,
  ): Promise<AgentRunDetail> {
    return this.request(
      `/api/v1/ai/approvals/${approvalId}/decision`,
      { method: "POST", body: JSON.stringify(decision) },
      true,
      true,
    );
  }

  addAgentFeedback(
    runId: string,
    feedback: FeedbackCreate,
  ): Promise<AgentFeedback> {
    return this.request(
      `/api/v1/ai/runs/${runId}/feedback`,
      { method: "POST", body: JSON.stringify(feedback) },
      true,
      true,
    );
  }

  knowledgeDocuments(): Promise<KnowledgeDocument[]> {
    return this.request("/api/v1/ai/knowledge", {}, true, true);
  }

  ingestKnowledge(
    document: KnowledgeDocumentCreate,
  ): Promise<KnowledgeDocument> {
    return this.request(
      "/api/v1/ai/knowledge",
      { method: "POST", body: JSON.stringify(document) },
      true,
      true,
    );
  }

  searchKnowledge(query: string): Promise<KnowledgeSearchResult[]> {
    return this.request(
      `/api/v1/ai/knowledge/search?query=${encodeURIComponent(query)}`,
      {},
      true,
      true,
    );
  }

  removeKnowledge(documentId: string): Promise<void> {
    return this.request(
      `/api/v1/ai/knowledge/${documentId}`,
      { method: "DELETE" },
      true,
      true,
    );
  }

  extractions(status?: string): Promise<ExtractionRun[]> {
    const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
    return this.request(`/api/v1/ai/extractions${suffix}`, {}, true, true);
  }

  createExtraction(extraction: ExtractionCreate): Promise<ExtractionRun> {
    return this.request(
      "/api/v1/ai/extractions",
      { method: "POST", body: JSON.stringify(extraction) },
      true,
      true,
    );
  }

  reviewExtraction(
    extractionId: string,
    review: ExtractionReview,
  ): Promise<ExtractionRun> {
    return this.request(
      `/api/v1/ai/extractions/${extractionId}/review`,
      { method: "POST", body: JSON.stringify(review) },
      true,
      true,
    );
  }

  automationTemplates(): Promise<AutomationTemplate[]> {
    return this.request("/api/v1/automations/templates", {}, true, true);
  }

  automations(): Promise<AutomationDefinition[]> {
    return this.request("/api/v1/automations", {}, true, true);
  }

  createAutomation(
    automation: AutomationDefinitionCreate,
  ): Promise<AutomationDefinition> {
    return this.request(
      "/api/v1/automations",
      { method: "POST", body: JSON.stringify(automation) },
      true,
      true,
    );
  }

  installAutomationTemplate(
    templateKey: string,
    name?: string,
  ): Promise<AutomationDefinition> {
    return this.request(
      `/api/v1/automations/templates/${templateKey}/install`,
      { method: "POST", body: JSON.stringify({ name: name ?? null }) },
      true,
      true,
    );
  }

  updateAutomation(
    automationId: string,
    automation: AutomationDefinitionUpdate,
  ): Promise<AutomationDefinition> {
    return this.request(
      `/api/v1/automations/definitions/${automationId}`,
      { method: "PUT", body: JSON.stringify(automation) },
      true,
      true,
    );
  }

  activateAutomation(
    automationId: string,
    mode: "shadow" | "production",
  ): Promise<AutomationDefinition> {
    return this.request(
      `/api/v1/automations/definitions/${automationId}/activate`,
      { method: "POST", body: JSON.stringify({ mode }) },
      true,
      true,
    );
  }

  pauseAutomation(automationId: string): Promise<AutomationDefinition> {
    return this.request(
      `/api/v1/automations/definitions/${automationId}/pause`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  killAutomation(
    automationId: string,
    reason: string,
  ): Promise<AutomationDefinition> {
    return this.request(
      `/api/v1/automations/definitions/${automationId}/kill`,
      { method: "POST", body: JSON.stringify({ reason }) },
      true,
      true,
    );
  }

  testAutomation(
    automationId: string,
    payload: Record<string, unknown>,
  ): Promise<AutomationRun> {
    return this.request(
      `/api/v1/automations/definitions/${automationId}/test`,
      { method: "POST", body: JSON.stringify({ payload }) },
      true,
      true,
    );
  }

  automationRuns(status?: string): Promise<AutomationRun[]> {
    const suffix = status ? `?run_status=${encodeURIComponent(status)}` : "";
    return this.request(
      `/api/v1/automations/runs/history${suffix}`,
      {},
      true,
      true,
    );
  }

  automationRun(runId: string): Promise<AutomationRun> {
    return this.request(`/api/v1/automations/runs/${runId}`, {}, true, true);
  }

  triggerAutomation(trigger: AutomationTrigger): Promise<AutomationRun[]> {
    return this.request(
      "/api/v1/automations/triggers",
      { method: "POST", body: JSON.stringify(trigger) },
      true,
      true,
    );
  }

  decideAutomationApproval(
    approvalId: string,
    decision: AutomationApprovalDecision,
  ): Promise<AutomationRun> {
    return this.request(
      `/api/v1/automations/approvals/${approvalId}/decision`,
      { method: "POST", body: JSON.stringify(decision) },
      true,
      true,
    );
  }

  retryAutomationRun(runId: string): Promise<AutomationRun> {
    return this.request(
      `/api/v1/automations/runs/${runId}/retry`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  replayAutomationRun(runId: string): Promise<AutomationRun> {
    return this.request(
      `/api/v1/automations/runs/${runId}/replay`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  compensateAutomationRun(
    runId: string,
  ): Promise<components["schemas"]["CompensationRead"]> {
    return this.request(
      `/api/v1/automations/runs/${runId}/compensate`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  automationMetrics(): Promise<AutomationMetrics> {
    return this.request("/api/v1/automations/metrics/summary", {}, true, true);
  }

  voiceConfig(): Promise<VoiceFlowConfig> {
    return this.request("/api/v1/voice/config", {}, true, true);
  }

  updateVoiceConfig(config: VoiceFlowConfigUpdate): Promise<VoiceFlowConfig> {
    return this.request(
      "/api/v1/voice/config",
      { method: "PUT", body: JSON.stringify(config) },
      true,
      true,
    );
  }

  voiceCalls(status?: string): Promise<VoiceCall[]> {
    const suffix = status ? `?call_status=${encodeURIComponent(status)}` : "";
    return this.request(`/api/v1/voice/calls${suffix}`, {}, true, true);
  }

  voiceCall(callId: string): Promise<VoiceCall> {
    return this.request(`/api/v1/voice/calls/${callId}`, {}, true, true);
  }

  reviewVoiceCall(
    callId: string,
    decision: VoiceReviewDecision,
  ): Promise<VoiceCall> {
    return this.request(
      `/api/v1/voice/calls/${callId}/review`,
      { method: "POST", body: JSON.stringify(decision) },
      true,
      true,
    );
  }

  transferVoiceCall(callId: string, reason: string): Promise<VoiceCall> {
    return this.request(
      `/api/v1/voice/calls/${callId}/transfer`,
      { method: "POST", body: JSON.stringify({ reason }) },
      true,
      true,
    );
  }

  simulateVoiceCall(simulation: VoiceSimulation): Promise<VoiceCall> {
    return this.request(
      "/api/v1/voice/simulations",
      { method: "POST", body: JSON.stringify(simulation) },
      true,
      true,
    );
  }

  voiceMetrics(): Promise<VoiceMetrics> {
    return this.request("/api/v1/voice/metrics", {}, true, true);
  }

  async voiceRecording(callId: string): Promise<Blob> {
    return (
      await this.fetchResponse(
        `/api/v1/voice/calls/${callId}/recording`,
        {},
        true,
        true,
      )
    ).blob();
  }

  intelligenceDashboard(): Promise<IntelligenceDashboard> {
    return this.request("/api/v1/intelligence/dashboard", {}, true, true);
  }

  intelligenceMetricDefinitions(): Promise<MetricDefinition[]> {
    return this.request(
      "/api/v1/intelligence/metric-definitions",
      {},
      true,
      true,
    );
  }

  intelligenceCalls(
    filters: {
      q?: string;
      topic?: string;
      reviewStatus?: string;
    } = {},
  ): Promise<IntelligenceSearchResult[]> {
    const query = new URLSearchParams();
    if (filters.q) query.set("q", filters.q);
    if (filters.topic) query.set("topic", filters.topic);
    if (filters.reviewStatus) query.set("review_status", filters.reviewStatus);
    const suffix = query.size ? `?${query}` : "";
    return this.request(`/api/v1/intelligence/calls${suffix}`, {}, true, true);
  }

  intelligenceCall(callId: string): Promise<CallIntelligenceDetail> {
    return this.request(`/api/v1/intelligence/calls/${callId}`, {}, true, true);
  }

  processIntelligenceCall(callId: string): Promise<CallIntelligenceDetail> {
    return this.request(
      `/api/v1/intelligence/calls/${callId}/process`,
      { method: "POST", body: "{}" },
      true,
      true,
    );
  }

  reviewIntelligence(
    callId: string,
    review: IntelligenceReview,
  ): Promise<CallIntelligenceDetail["intelligence"]> {
    return this.request(
      `/api/v1/intelligence/calls/${callId}/review`,
      { method: "POST", body: JSON.stringify(review) },
      true,
      true,
    );
  }

  correctCallTranscript(
    callId: string,
    correction: TranscriptCorrection,
  ): Promise<CallIntelligenceDetail> {
    return this.request(
      `/api/v1/intelligence/calls/${callId}/transcript-corrections`,
      { method: "POST", body: JSON.stringify(correction) },
      true,
      true,
    );
  }

  reconcileCall(
    callId: string,
    snapshot: ReconciliationCreate,
  ): Promise<Reconciliation> {
    return this.request(
      `/api/v1/intelligence/calls/${callId}/reconcile`,
      { method: "POST", body: JSON.stringify(snapshot) },
      true,
      true,
    );
  }

  async streamAgentRun(
    runId: string,
    after: number,
    onEvent: (event: AgentEvent) => void,
    signal?: AbortSignal,
  ): Promise<number> {
    const response = await this.fetchResponse(
      `/api/v1/ai/runs/${runId}/stream?after=${after}`,
      { headers: { Accept: "text/event-stream" }, signal },
      true,
      true,
    );
    if (!response.body?.getReader) {
      const events = await this.agentRunEvents(runId, after);
      events.forEach(onEvent);
      return events.at(-1)?.sequence ?? after;
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let cursor = after;
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const data = block
          .split("\n")
          .find((line) => line.startsWith("data: "))
          ?.slice(6);
        if (!data) continue;
        const event = JSON.parse(data) as AgentEvent;
        cursor = event.sequence;
        onEvent(event);
      }
      if (done) return cursor;
    }
  }

  private command<T>(path: string, body: unknown): Promise<T> {
    const idempotencyKey = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    return this.request(
      path,
      {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: JSON.stringify(body),
      },
      true,
      true,
    );
  }

  private async request<T>(
    path: string,
    init: RequestInit = {},
    authenticated = true,
    tenantScoped = false,
  ): Promise<T> {
    const response = await this.fetchResponse(
      path,
      init,
      authenticated,
      tenantScoped,
    );
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  private async fetchResponse(
    path: string,
    init: RequestInit = {},
    authenticated = true,
    tenantScoped = false,
  ): Promise<Response> {
    const headers = new Headers(init.headers);
    if (!(init.body instanceof FormData))
      headers.set("Content-Type", "application/json");
    if (authenticated) {
      const token = await this.options.getToken();
      if (token) headers.set("Authorization", `Bearer ${token}`);
    }
    if (tenantScoped) {
      const tenantId = await this.options.getTenantId();
      if (tenantId) headers.set("X-Tenant-ID", tenantId);
    }
    const response = await fetch(`${this.options.baseUrl}${path}`, {
      ...init,
      headers,
    });
    if (!response.ok) {
      const payload = (await response.json().catch(() => null)) as {
        detail?: string;
      } | null;
      throw new ApiError(
        response.status,
        payload?.detail ?? `Request failed (${response.status})`,
      );
    }
    return response;
  }
}
