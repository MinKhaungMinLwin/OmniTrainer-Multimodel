import { zodResolver } from "@hookform/resolvers/zod";
import {
  AgentEvent,
  AgentRunDetail,
  Approval,
  Appointment,
  AutomationDefinition,
  AutomationRun,
  CallIntelligenceDetail,
  ConversationMessage,
  CustomerCreate,
  DraftInvoice,
  Invoice,
  Job,
  JobCreate,
  OmniApiClient,
  Tenant,
  VoiceCall,
} from "@omni/contracts";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8001";
const TOKEN_KEY = "omni.access-token";
const TENANT_KEY = "omni.tenant-id";
const STOP_SPEECH_EVENT = "omni:stop-speech";
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1 } },
});

function stopActiveSpeech() {
  window.dispatchEvent(new Event(STOP_SPEECH_EVENT));
}

export function microphoneErrorMessage(error: unknown): string {
  if (error instanceof DOMException) {
    if (["NotAllowedError", "SecurityError"].includes(error.name))
      return "Microphone access is blocked. Open this site's permissions in your browser, allow Microphone, then try again.";
    if (["NotFoundError", "DevicesNotFoundError"].includes(error.name))
      return "No microphone was found. Connect or enable a microphone, then try again.";
    if (["NotReadableError", "TrackStartError"].includes(error.name))
      return "The microphone is busy in another application. Close the other recording app, then try again.";
  }
  return error instanceof Error ? error.message : "Microphone access failed";
}

const customerSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200),
  email: z.union([z.literal(""), z.email()]).optional(),
  phone: z.string().max(50).optional(),
  notes: z.string().max(5000).optional(),
});

type CustomerForm = z.infer<typeof customerSchema>;
type Section =
  | "customers"
  | "jobs"
  | "schedule"
  | "invoices"
  | "team"
  | "assistant"
  | "automations"
  | "voice"
  | "intelligence";

function useOnlineStatus() {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return online;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <OmniShell />
    </QueryClientProvider>
  );
}

function OmniShell() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [tenantId, setTenantId] = useState(() =>
    localStorage.getItem(TENANT_KEY),
  );
  const api = useMemo(
    () =>
      new OmniApiClient({
        baseUrl: API_URL,
        getToken: () => token,
        getTenantId: () => tenantId,
      }),
    [token, tenantId],
  );

  if (!token) {
    return (
      <SignIn
        api={api}
        onAuthenticated={(nextToken) => {
          localStorage.setItem(TOKEN_KEY, nextToken);
          setToken(nextToken);
        }}
      />
    );
  }

  return (
    <AuthenticatedApp
      api={api}
      tenantId={tenantId}
      onTenantChange={(id) => {
        localStorage.setItem(TENANT_KEY, id);
        setTenantId(id);
      }}
      onSignOut={() => {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(TENANT_KEY);
        setToken(null);
        setTenantId(null);
      }}
    />
  );
}

function SignIn({
  api,
  onAuthenticated,
}: {
  api: OmniApiClient;
  onAuthenticated: (token: string) => void;
}) {
  const [email, setEmail] = useState("owner@omni.example");
  const mutation = useMutation({
    mutationFn: () => api.createDevelopmentToken(email),
    onSuccess: onAuthenticated,
  });
  return (
    <main className="auth-page">
      <section className="auth-card">
        <span className="eyebrow">Omni Model</span>
        <h1>Run your operation from one calm workspace.</h1>
        <p>
          Customers, jobs, schedules, invoices, and AI-assisted work—connected
          by design.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
        >
          <label htmlFor="email">Development account</label>
          <input
            id="email"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          {mutation.error && (
            <div className="error-banner">{mutation.error.message}</div>
          )}
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Signing in…" : "Continue"}
          </button>
        </form>
        <small>
          Development sign-in is disabled automatically in production.
        </small>
      </section>
    </main>
  );
}

function AuthenticatedApp({
  api,
  tenantId,
  onTenantChange,
  onSignOut,
}: {
  api: OmniApiClient;
  tenantId: string | null;
  onTenantChange: (id: string) => void;
  onSignOut: () => void;
}) {
  const [section, setSection] = useState<Section>("customers");
  const online = useOnlineStatus();
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api.tenants(),
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 30_000,
  });
  const activeTenant =
    tenants.data?.find((tenant) => tenant.id === tenantId)?.id ??
    tenants.data?.[0]?.id ??
    null;
  const pageTenant = tenantId === activeTenant ? activeTenant : null;

  useEffect(() => {
    if (!tenantId && activeTenant) onTenantChange(activeTenant);
  }, [activeTenant, onTenantChange, tenantId]);

  if (tenants.isPending)
    return <FullPageState title="Opening your workspace…" />;
  if (tenants.isError)
    return (
      <FullPageState
        title="We couldn’t load your workspace."
        detail={tenants.error.message}
        action={onSignOut}
      />
    );
  if (!tenants.data.length)
    return (
      <FullPageState
        title="No workspace assigned"
        detail="Ask an administrator for access."
      />
    );
  return (
    <div className="app-shell">
      <aside>
        <div className="brand">OM</div>
        <nav aria-label="Primary">
          <button
            className={section === "customers" ? "active" : ""}
            onClick={() => setSection("customers")}
          >
            Customers
          </button>
          <button
            className={section === "jobs" ? "active" : ""}
            onClick={() => setSection("jobs")}
          >
            Jobs
          </button>
          <button
            className={section === "schedule" ? "active" : ""}
            onClick={() => setSection("schedule")}
          >
            Schedule
          </button>
          <button
            className={section === "invoices" ? "active" : ""}
            onClick={() => setSection("invoices")}
          >
            Invoices
          </button>
          <button
            className={section === "team" ? "active" : ""}
            onClick={() => setSection("team")}
          >
            Team
          </button>
          <button
            className={section === "assistant" ? "active" : ""}
            onClick={() => setSection("assistant")}
          >
            Assistant <em>AI</em>
          </button>
          <button
            className={section === "automations" ? "active" : ""}
            onClick={() => setSection("automations")}
          >
            Automations
          </button>
          <button
            className={section === "voice" ? "active" : ""}
            onClick={() => setSection("voice")}
          >
            Voice <em>Pilot</em>
          </button>
          <button
            className={section === "intelligence" ? "active" : ""}
            onClick={() => setSection("intelligence")}
          >
            Intelligence
          </button>
        </nav>
      </aside>
      <div className="workspace">
        {!online && (
          <div className="offline-banner" role="status">
            Offline — showing cached records. Changes are temporarily disabled.
          </div>
        )}
        <header>
          <select
            aria-label="Workspace"
            value={activeTenant ?? ""}
            onChange={(event) => onTenantChange(event.target.value)}
          >
            {tenants.data.map((tenant: Tenant) => (
              <option key={tenant.id} value={tenant.id}>
                {tenant.name}
              </option>
            ))}
          </select>
          <div className={`connection ${health.isError ? "offline" : ""}`}>
            <span />
            {health.isError
              ? "API unavailable"
              : health.isPending
                ? "Checking…"
                : "Connected"}
          </div>
          <button className="ghost" onClick={onSignOut}>
            Sign out
          </button>
        </header>
        <fieldset className="page-boundary" disabled={!online}>
          {pageTenant && section === "customers" && (
            <CustomersPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "jobs" && (
            <JobsPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "schedule" && (
            <SchedulePage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "invoices" && (
            <InvoicesPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "team" && (
            <TeamPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "assistant" && (
            <AssistantPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "automations" && (
            <AutomationsPage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "voice" && (
            <VoicePage api={api} tenantId={pageTenant} />
          )}
          {pageTenant && section === "intelligence" && (
            <IntelligencePage api={api} tenantId={pageTenant} />
          )}
        </fieldset>
      </div>
    </div>
  );
}

function CustomersPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(
    null,
  );
  const customers = useQuery({
    queryKey: ["customers", tenantId, search],
    queryFn: () => api.customers(search),
  });
  const form = useForm<CustomerForm>({
    resolver: zodResolver(customerSchema),
    defaultValues: { name: "", email: "", phone: "", notes: "" },
  });
  const create = useMutation({
    mutationFn: (values: CustomerForm) =>
      api.createCustomer({
        name: values.name,
        email: values.email || null,
        phone: values.phone || null,
        notes: values.notes || null,
        external_ref: null,
      } as CustomerCreate),
    onSuccess: async () => {
      form.reset();
      setShowForm(false);
      await cache.invalidateQueries({ queryKey: ["customers", tenantId] });
    },
  });

  return (
    <main className="content" id="customers">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Relationships</span>
          <h1>Customers</h1>
          <p>Every customer and their history, ready for the next job.</p>
        </div>
        <button onClick={() => setShowForm((value) => !value)}>
          {showForm ? "Cancel" : "+ New customer"}
        </button>
      </div>
      <label className="search-box">
        <span className="sr-only">Search customers</span>
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search customers"
        />
      </label>
      {showForm && (
        <form
          className="customer-form"
          onSubmit={form.handleSubmit((values) => create.mutate(values))}
        >
          <label>
            Name
            <input {...form.register("name")} placeholder="Acme Services" />
          </label>
          <label>
            Email
            <input
              {...form.register("email")}
              type="email"
              placeholder="ops@example.com"
            />
          </label>
          <label>
            Phone
            <input {...form.register("phone")} placeholder="+1 555 0100" />
          </label>
          <label className="wide">
            Notes
            <textarea {...form.register("notes")} />
          </label>
          {form.formState.errors.name && (
            <span className="field-error">
              {form.formState.errors.name.message}
            </span>
          )}
          {create.error && (
            <div className="error-banner wide">{create.error.message}</div>
          )}
          <button type="submit" disabled={create.isPending}>
            {create.isPending ? "Saving…" : "Save customer"}
          </button>
        </form>
      )}
      {customers.isPending && <div className="panel">Loading customers…</div>}
      {customers.isError && (
        <div className="error-banner">{customers.error.message}</div>
      )}
      {customers.data?.total === 0 && (
        <div className="empty">
          <div>◎</div>
          <h2>No customers yet</h2>
          <p>Add the first customer to begin the workflow.</p>
        </div>
      )}
      {!!customers.data?.total && (
        <div className="customer-grid">
          {customers.data.items.map((customer) => (
            <article key={customer.id}>
              <div className="avatar">
                {customer.name.slice(0, 2).toUpperCase()}
              </div>
              <div>
                <h2>{customer.name}</h2>
                <p>
                  {customer.email ?? customer.phone ?? "No contact details"}
                </p>
              </div>
              <button
                className="secondary"
                onClick={() => setSelectedCustomerId(customer.id)}
              >
                View
              </button>
            </article>
          ))}
        </div>
      )}
      {selectedCustomerId && (
        <CustomerDetailPanel
          key={selectedCustomerId}
          api={api}
          customerId={selectedCustomerId}
          tenantId={tenantId}
          onClose={() => setSelectedCustomerId(null)}
        />
      )}
    </main>
  );
}

function CustomerDetailPanel({
  api,
  customerId,
  tenantId,
  onClose,
}: {
  api: OmniApiClient;
  customerId: string;
  tenantId: string;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const detail = useQuery({
    queryKey: ["customer", tenantId, customerId],
    queryFn: () => api.customer(customerId),
  });
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["customer", tenantId, customerId] }),
      cache.invalidateQueries({ queryKey: ["customers", tenantId] }),
    ]);
  };
  const update = useMutation({
    mutationFn: (values: FormData) =>
      api.updateCustomer(customerId, {
        name: String(values.get("name")),
        email: String(values.get("email")) || null,
        phone: String(values.get("phone")) || null,
        external_ref: detail.data?.external_ref ?? null,
        notes: String(values.get("notes")) || null,
        expected_version: detail.data!.version,
      }),
    onSuccess: refresh,
  });
  const contact = useMutation({
    mutationFn: (values: FormData) =>
      api.addContact(customerId, {
        name: String(values.get("name")),
        email: String(values.get("email")) || null,
        phone: null,
        role: String(values.get("role")) || null,
        is_primary: detail.data?.contacts.length === 0,
      }),
    onSuccess: refresh,
  });
  const location = useMutation({
    mutationFn: (values: FormData) =>
      api.addLocation(customerId, {
        label: String(values.get("label")),
        address_line1: String(values.get("address")),
        address_line2: null,
        city: String(values.get("city")),
        region: null,
        postal_code: null,
        country: String(values.get("country") || "US").toUpperCase(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      }),
    onSuccess: refresh,
  });
  const error = update.error ?? contact.error ?? location.error;
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Customer details"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button
          className="ghost drawer-close"
          onClick={onClose}
          aria-label="Close customer details"
        >
          ×
        </button>
        {detail.isPending && <div className="panel">Loading customer…</div>}
        {detail.isError && (
          <div className="error-banner">{detail.error.message}</div>
        )}
        {detail.data && (
          <>
            <span className="eyebrow">Customer record</span>
            <h1>{detail.data.name}</h1>
            <form
              className="stack-form"
              action={(values) => update.mutate(values)}
            >
              <label>
                Name
                <input name="name" defaultValue={detail.data.name} required />
              </label>
              <label>
                Email
                <input
                  name="email"
                  type="email"
                  defaultValue={detail.data.email ?? ""}
                />
              </label>
              <label>
                Phone
                <input name="phone" defaultValue={detail.data.phone ?? ""} />
              </label>
              <label>
                Notes
                <textarea name="notes" defaultValue={detail.data.notes ?? ""} />
              </label>
              <button disabled={update.isPending}>Save customer</button>
            </form>
            {error && <div className="error-banner">{error.message}</div>}
            <DetailCollection title="Contacts" empty="No contacts added.">
              {detail.data.contacts.map((item) => (
                <p key={item.id}>
                  <strong>{item.name}</strong>
                  <br />
                  {item.email ?? item.phone ?? item.role}
                </p>
              ))}
              <form
                className="mini-form"
                action={(values) => contact.mutate(values)}
              >
                <input name="name" placeholder="Contact name" required />
                <input name="email" type="email" placeholder="Email" />
                <input name="role" placeholder="Role" />
                <button>Add</button>
              </form>
            </DetailCollection>
            <DetailCollection title="Locations" empty="No locations added.">
              {detail.data.locations.map((item) => (
                <p key={item.id}>
                  <strong>{item.label}</strong>
                  <br />
                  {item.address_line1}, {item.city}
                </p>
              ))}
              <form
                className="mini-form"
                action={(values) => location.mutate(values)}
              >
                <input name="label" placeholder="Label" required />
                <input name="address" placeholder="Address" required />
                <input name="city" placeholder="City" required />
                <input name="country" placeholder="US" maxLength={2} required />
                <button>Add</button>
              </form>
            </DetailCollection>
            <DetailCollection title="Activity" empty="No activity yet.">
              {detail.data.activity.map((item) => (
                <p key={item.id}>
                  <strong>{item.action.replaceAll(".", " ")}</strong>
                  <br />
                  <time>{new Date(item.occurred_at).toLocaleString()}</time>
                </p>
              ))}
            </DetailCollection>
          </>
        )}
      </section>
    </div>
  );
}

function DetailCollection({
  title,
  empty,
  children,
}: {
  title: string;
  empty: string;
  children: React.ReactNode;
}) {
  const values = Array.isArray(children)
    ? children.filter(Boolean)
    : [children];
  return (
    <section className="detail-section">
      <h2>{title}</h2>
      {values.length ? children : <p>{empty}</p>}
    </section>
  );
}

function JobsPage({ api, tenantId }: { api: OmniApiClient; tenantId: string }) {
  const cache = useQueryClient();
  const [customerId, setCustomerId] = useState("");
  const [title, setTitle] = useState("");
  const [scheduling, setScheduling] = useState<Job | null>(null);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [technicianId, setTechnicianId] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const customers = useQuery({
    queryKey: ["customers", tenantId],
    queryFn: () => api.customers(),
  });
  const jobs = useQuery({
    queryKey: ["jobs", tenantId],
    queryFn: () => api.jobs(),
  });
  const selectedCustomerId = customerId || customers.data?.items[0]?.id || "";
  const customerDetail = useQuery({
    queryKey: ["customer", tenantId, selectedCustomerId],
    queryFn: () => api.customer(selectedCustomerId),
    enabled: Boolean(selectedCustomerId),
  });
  const technicians = useQuery({
    queryKey: ["technicians", tenantId],
    queryFn: () => api.technicians(),
  });
  const refresh = () =>
    cache.invalidateQueries({ queryKey: ["jobs", tenantId] });
  const create = useMutation({
    mutationFn: () =>
      api.createJob({
        customer_id: selectedCustomerId,
        location_id: customerDetail.data?.locations[0]?.id ?? null,
        title,
      } as JobCreate),
    onSuccess: async () => {
      setTitle("");
      await refresh();
    },
  });
  const schedule = useMutation({
    mutationFn: () =>
      api.scheduleJob(scheduling!.id, {
        starts_at: new Date(startsAt).toISOString(),
        ends_at: new Date(endsAt).toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        assignee: null,
        technician_id: technicianId || null,
        expected_version: scheduling!.version,
      }),
    onSuccess: async () => {
      setScheduling(null);
      setStartsAt("");
      setEndsAt("");
      setTechnicianId("");
      await Promise.all([
        refresh(),
        cache.invalidateQueries({ queryKey: ["appointments", tenantId] }),
      ]);
    },
  });
  const complete = useMutation({
    mutationFn: (job: Job) => api.completeJob(job.id, job.version),
    onSuccess: refresh,
  });
  return (
    <main className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Operations</span>
          <h1>Jobs</h1>
          <p>
            Create, schedule, and complete work with explicit lifecycle
            commands.
          </p>
        </div>
      </div>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <select
          aria-label="Customer"
          value={selectedCustomerId}
          onChange={(event) => setCustomerId(event.target.value)}
        >
          <option value="">Select customer</option>
          {customers.data?.items.map((customer) => (
            <option key={customer.id} value={customer.id}>
              {customer.name}
            </option>
          ))}
        </select>
        <input
          aria-label="Job title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="Job title"
        />
        <button
          disabled={!selectedCustomerId || !title.trim() || create.isPending}
        >
          Create job
        </button>
      </form>
      {(create.error || schedule.error || complete.error) && (
        <div className="error-banner">
          {(create.error ?? schedule.error ?? complete.error)?.message}
        </div>
      )}
      {scheduling && (
        <form
          className="inline-form schedule-form"
          onSubmit={(event) => {
            event.preventDefault();
            schedule.mutate();
          }}
        >
          <strong>Schedule {scheduling.title}</strong>
          <input
            aria-label="Starts at"
            type="datetime-local"
            value={startsAt}
            onChange={(event) => setStartsAt(event.target.value)}
          />
          <select
            aria-label="Technician"
            value={technicianId}
            onChange={(event) => setTechnicianId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {technicians.data
              ?.filter((item) => item.is_active)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
          <input
            aria-label="Ends at"
            type="datetime-local"
            value={endsAt}
            onChange={(event) => setEndsAt(event.target.value)}
          />
          <button disabled={!startsAt || !endsAt || schedule.isPending}>
            Confirm schedule
          </button>
        </form>
      )}
      {!jobs.data?.total && (
        <div className="empty">
          <h2>No jobs yet</h2>
          <p>Create a customer, then add the first job.</p>
        </div>
      )}
      <div className="records">
        {jobs.data?.items.map((job) => (
          <article key={job.id}>
            <div className={`status-dot ${job.status}`} />
            <div>
              <h2>{job.title}</h2>
              <p>
                Version {job.version} · {job.status}
              </p>
            </div>
            <div className="row-actions">
              <button className="ghost" onClick={() => setSelectedJob(job)}>
                Details
              </button>
              {job.status === "draft" && (
                <button
                  className="secondary"
                  onClick={() => setScheduling(job)}
                >
                  Schedule
                </button>
              )}
              {job.status === "scheduled" && (
                <button onClick={() => complete.mutate(job)}>Complete</button>
              )}
            </div>
          </article>
        ))}
      </div>
      {selectedJob && (
        <JobDetailPanel
          api={api}
          job={selectedJob}
          tenantId={tenantId}
          onClose={() => setSelectedJob(null)}
        />
      )}
    </main>
  );
}

function JobDetailPanel({
  api,
  job,
  tenantId,
  onClose,
}: {
  api: OmniApiClient;
  job: Job;
  tenantId: string;
  onClose: () => void;
}) {
  const cache = useQueryClient();
  const notes = useQuery({
    queryKey: ["job-notes", tenantId, job.id],
    queryFn: () => api.jobNotes(job.id),
  });
  const history = useQuery({
    queryKey: ["job-history", tenantId, job.id],
    queryFn: () => api.jobHistory(job.id),
  });
  const attachments = useQuery({
    queryKey: ["job-attachments", tenantId, job.id],
    queryFn: () => api.jobAttachments(job.id),
  });
  const addNote = useMutation({
    mutationFn: (values: FormData) =>
      api.addJobNote(job.id, String(values.get("body"))),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["job-notes", tenantId, job.id] }),
  });
  const upload = useMutation({
    mutationFn: (values: FormData) => {
      const file = values.get("file");
      if (!(file instanceof File)) throw new Error("Choose a file to upload");
      return api.uploadJobAttachment(job.id, file, file.name);
    },
    onSuccess: () =>
      cache.invalidateQueries({
        queryKey: ["job-attachments", tenantId, job.id],
      }),
  });
  const download = async (id: string, filename: string) => {
    const blob = await api.attachmentContent(id);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="Job details"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button
          className="ghost drawer-close"
          onClick={onClose}
          aria-label="Close job details"
        >
          ×
        </button>
        <span className="eyebrow">{job.status}</span>
        <h1>{job.title}</h1>
        <p>{job.description ?? "No job description."}</p>
        <DetailCollection title="Notes" empty="No notes yet.">
          {notes.data?.map((note) => (
            <p key={note.id}>
              {note.body}
              <br />
              <time>{new Date(note.created_at).toLocaleString()}</time>
            </p>
          ))}
          <form
            className="mini-form"
            action={(values) => addNote.mutate(values)}
          >
            <input name="body" placeholder="Add a field note" required />
            <button>Add note</button>
          </form>
        </DetailCollection>
        <DetailCollection title="Attachments" empty="No attachments yet.">
          {attachments.data?.map((item) => (
            <p key={item.id}>
              <button
                className="link-button"
                onClick={() => void download(item.id, item.filename)}
              >
                {item.filename}
              </button>{" "}
              · {(item.size_bytes / 1024).toFixed(1)} KB
            </p>
          ))}
          <form
            className="mini-form"
            action={(values) => upload.mutate(values)}
          >
            <input name="file" type="file" required />
            <button>Upload</button>
          </form>
        </DetailCollection>
        <DetailCollection title="Status history" empty="No history yet.">
          {history.data?.map((item) => (
            <p key={item.id}>
              <strong>{item.to_status}</strong>
              <br />
              <time>{new Date(item.occurred_at).toLocaleString()}</time>
            </p>
          ))}
        </DetailCollection>
        {(addNote.error || upload.error) && (
          <div className="error-banner">
            {(addNote.error ?? upload.error)?.message}
          </div>
        )}
      </section>
    </div>
  );
}

function SchedulePage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [rescheduling, setRescheduling] = useState<Appointment | null>(null);
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [technicianId, setTechnicianId] = useState("");
  const [weekOffset, setWeekOffset] = useState(0);
  const appointments = useQuery({
    queryKey: ["appointments", tenantId],
    queryFn: () => api.appointments(),
  });
  const technicians = useQuery({
    queryKey: ["technicians", tenantId],
    queryFn: () => api.technicians(),
  });
  const reschedule = useMutation({
    mutationFn: () =>
      api.rescheduleAppointment(rescheduling!.id, {
        starts_at: new Date(startsAt).toISOString(),
        ends_at: new Date(endsAt).toISOString(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        technician_id: technicianId || null,
        expected_version: rescheduling!.version,
      }),
    onSuccess: async () => {
      setRescheduling(null);
      await cache.invalidateQueries({ queryKey: ["appointments", tenantId] });
    },
  });
  const chooseAppointment = (appointment: Appointment) => {
    const localValue = (value: string) => {
      const date = new Date(value);
      return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
        .toISOString()
        .slice(0, 16);
    };
    setRescheduling(appointment);
    setStartsAt(localValue(appointment.starts_at));
    setEndsAt(localValue(appointment.ends_at));
    setTechnicianId(appointment.technician_id ?? "");
  };
  const weekDays = useMemo(() => {
    const today = new Date();
    const monday = new Date(today);
    monday.setHours(0, 0, 0, 0);
    monday.setDate(
      today.getDate() - ((today.getDay() + 6) % 7) + weekOffset * 7,
    );
    return Array.from({ length: 7 }, (_, index) => {
      const date = new Date(monday);
      date.setDate(monday.getDate() + index);
      return date;
    });
  }, [weekOffset]);
  return (
    <main className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Dispatch</span>
          <h1>Schedule</h1>
          <p>
            All appointment times are stored in UTC with their display timezone.
          </p>
        </div>
        <div className="row-actions">
          <button
            className="secondary"
            onClick={() => setWeekOffset((value) => value - 1)}
          >
            Previous
          </button>
          <button className="ghost" onClick={() => setWeekOffset(0)}>
            Today
          </button>
          <button
            className="secondary"
            onClick={() => setWeekOffset((value) => value + 1)}
          >
            Next
          </button>
        </div>
      </div>
      {rescheduling && (
        <form
          className="inline-form schedule-form"
          onSubmit={(event) => {
            event.preventDefault();
            reschedule.mutate();
          }}
        >
          <input
            aria-label="New start"
            type="datetime-local"
            value={startsAt}
            onChange={(event) => setStartsAt(event.target.value)}
          />
          <input
            aria-label="New end"
            type="datetime-local"
            value={endsAt}
            onChange={(event) => setEndsAt(event.target.value)}
          />
          <select
            aria-label="Technician"
            value={technicianId}
            onChange={(event) => setTechnicianId(event.target.value)}
          >
            <option value="">Unassigned</option>
            {technicians.data
              ?.filter((item) => item.is_active)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
          <button disabled={reschedule.isPending}>Save schedule</button>
        </form>
      )}
      {reschedule.error && (
        <div className="error-banner">{reschedule.error.message}</div>
      )}
      {!appointments.data?.length && (
        <div className="empty">
          <h2>No appointments</h2>
          <p>Schedule a draft job to place it here.</p>
        </div>
      )}
      <div className="calendar-board">
        {weekDays.map((day) => {
          const dayAppointments =
            appointments.data?.filter(
              (appointment) =>
                new Date(appointment.starts_at).toDateString() ===
                day.toDateString(),
            ) ?? [];
          return (
            <section key={day.toISOString()} className="calendar-column">
              <h2>
                {day.toLocaleDateString(undefined, { weekday: "short" })}
                <span>{day.getDate()}</span>
              </h2>
              {dayAppointments.map((appointment) => (
                <button
                  key={appointment.id}
                  className="appointment-card"
                  onClick={() => chooseAppointment(appointment)}
                >
                  <strong>
                    {new Date(appointment.starts_at).toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </strong>
                  <span>{appointment.assignee ?? "Unassigned"}</span>
                </button>
              ))}
            </section>
          );
        })}
      </div>
    </main>
  );
}

function TeamPage({ api, tenantId }: { api: OmniApiClient; tenantId: string }) {
  const cache = useQueryClient();
  const technicians = useQuery({
    queryKey: ["technicians", tenantId],
    queryFn: () => api.technicians(),
  });
  const create = useMutation({
    mutationFn: (values: FormData) =>
      api.createTechnician({
        name: String(values.get("name")),
        email: String(values.get("email")),
        phone: null,
        timezone: String(values.get("timezone") || "UTC"),
      }),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["technicians", tenantId] }),
  });
  const availability = useMutation({
    mutationFn: ({
      technicianId,
      values,
    }: {
      technicianId: string;
      values: FormData;
    }) => {
      const minutes = (value: FormDataEntryValue | null) => {
        const [hours, minute] = String(value).split(":").map(Number);
        return (hours ?? 0) * 60 + (minute ?? 0);
      };
      return api.addTechnicianAvailability(technicianId, {
        weekday: Number(values.get("weekday")),
        start_minute: minutes(values.get("start")),
        end_minute: minutes(values.get("end")),
      });
    },
  });
  return (
    <main className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Resources</span>
          <h1>Team & availability</h1>
          <p>
            Scheduling validates each technician’s local working hours and
            conflicts.
          </p>
        </div>
      </div>
      <form className="inline-form" action={(values) => create.mutate(values)}>
        <input name="name" placeholder="Technician name" required />
        <input name="email" type="email" placeholder="Email" required />
        <input
          name="timezone"
          defaultValue="UTC"
          placeholder="Timezone"
          required
        />
        <button disabled={create.isPending}>Add technician</button>
      </form>
      {(create.error || availability.error) && (
        <div className="error-banner">
          {(create.error ?? availability.error)?.message}
        </div>
      )}
      <div className="records">
        {technicians.data?.map((technician) => (
          <article className="team-card" key={technician.id}>
            <div className="avatar">
              {technician.name.slice(0, 2).toUpperCase()}
            </div>
            <div>
              <h2>{technician.name}</h2>
              <p>
                {technician.email} · {technician.timezone}
              </p>
            </div>
            <form
              className="availability-form"
              action={(values) =>
                availability.mutate({ technicianId: technician.id, values })
              }
            >
              <select
                name="weekday"
                aria-label={`Weekday for ${technician.name}`}
                defaultValue="0"
              >
                {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                  (day, index) => (
                    <option key={day} value={index}>
                      {day}
                    </option>
                  ),
                )}
              </select>
              <input
                name="start"
                type="time"
                defaultValue="09:00"
                aria-label="Available from"
              />
              <input
                name="end"
                type="time"
                defaultValue="17:00"
                aria-label="Available until"
              />
              <button className="secondary">Add hours</button>
            </form>
          </article>
        ))}
      </div>
    </main>
  );
}

function InvoicesPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [jobId, setJobId] = useState("");
  const [description, setDescription] = useState("Service work");
  const [price, setPrice] = useState("100.00");
  const [editingInvoiceId, setEditingInvoiceId] = useState<string | null>(null);
  const [payingInvoiceId, setPayingInvoiceId] = useState<string | null>(null);
  const jobs = useQuery({
    queryKey: ["jobs", tenantId],
    queryFn: () => api.jobs(),
  });
  const invoices = useQuery({
    queryKey: ["invoices", tenantId],
    queryFn: () => api.invoices(),
  });
  const invoicedJobIds = new Set(
    invoices.data?.items.map((invoice) => invoice.job_id) ?? [],
  );
  const completedJobs =
    jobs.data?.items.filter(
      (job) => job.status === "completed" && !invoicedJobIds.has(job.id),
    ) ?? [];
  const selectedJobId = jobId || completedJobs[0]?.id || "";
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["jobs", tenantId] }),
      cache.invalidateQueries({ queryKey: ["invoices", tenantId] }),
    ]);
  };
  const draft = useMutation({
    mutationFn: () => {
      const job = completedJobs.find((item) => item.id === selectedJobId)!;
      return api.draftInvoice(selectedJobId, {
        expected_job_version: job.version,
        currency: "USD",
        lines: [
          {
            description,
            quantity: 1,
            unit_price_cents: Math.round(Number(price) * 100),
          },
        ],
      } as DraftInvoice);
    },
    onSuccess: refresh,
  });
  const issue = useMutation({
    mutationFn: (invoice: { id: string; version: number }) =>
      api.issueInvoice(invoice.id, invoice.version),
    onSuccess: refresh,
  });
  const voidInvoice = useMutation({
    mutationFn: (invoice: { id: string; version: number }) =>
      api.voidInvoice(invoice.id, invoice.version),
    onSuccess: refresh,
  });
  const editLines = useMutation({
    mutationFn: ({ invoice, values }: { invoice: Invoice; values: FormData }) =>
      api.updateInvoiceLines(invoice.id, invoice.version, [
        {
          description: String(values.get("description")),
          quantity: Number(values.get("quantity")),
          unit_price_cents: Math.round(Number(values.get("price")) * 100),
        },
      ]),
    onSuccess: async () => {
      setEditingInvoiceId(null);
      await refresh();
    },
  });
  const payment = useMutation({
    mutationFn: ({
      invoiceId,
      values,
    }: {
      invoiceId: string;
      values: FormData;
    }) =>
      api.recordPayment(invoiceId, {
        amount_cents: Math.round(Number(values.get("amount")) * 100),
        method: String(values.get("method")),
        external_ref: null,
        received_at: null,
      }),
    onSuccess: async () => {
      setPayingInvoiceId(null);
      await refresh();
    },
  });
  const openPdf = async (invoiceId: string) => {
    const blob = await api.invoicePdf(invoiceId);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener,noreferrer");
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const error =
    draft.error ??
    issue.error ??
    voidInvoice.error ??
    editLines.error ??
    payment.error;
  return (
    <main className="content">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Billing</span>
          <h1>Invoices</h1>
          <p>Draft from completed work, then explicitly issue or void.</p>
        </div>
      </div>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault();
          draft.mutate();
        }}
      >
        <select
          aria-label="Completed job"
          value={selectedJobId}
          onChange={(event) => setJobId(event.target.value)}
        >
          <option value="">Select completed job</option>
          {completedJobs.map((job) => (
            <option key={job.id} value={job.id}>
              {job.title}
            </option>
          ))}
        </select>
        <input
          aria-label="Line description"
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
        <input
          aria-label="Price"
          type="number"
          min="0"
          step="0.01"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
        />
        <button
          disabled={!selectedJobId || !description.trim() || draft.isPending}
        >
          Draft invoice
        </button>
      </form>
      {error && <div className="error-banner">{error.message}</div>}
      {!invoices.data?.total && (
        <div className="empty">
          <h2>No invoices</h2>
          <p>Complete a job to prepare its invoice.</p>
        </div>
      )}
      <div className="records">
        {invoices.data?.items.map((invoice) => (
          <article key={invoice.id}>
            <div className="invoice-mark">$</div>
            <div>
              <h2>{invoice.number}</h2>
              <p>
                {new Intl.NumberFormat("en-US", {
                  style: "currency",
                  currency: invoice.currency,
                }).format(invoice.total_cents / 100)}{" "}
                · {invoice.status} · {invoice.payment_status}
              </p>
              {invoice.paid_cents > 0 && (
                <p>
                  Paid {(invoice.paid_cents / 100).toFixed(2)}{" "}
                  {invoice.currency}
                </p>
              )}
            </div>
            <div className="row-actions">
              <button
                className="ghost"
                onClick={() => void openPdf(invoice.id)}
              >
                PDF
              </button>
              {invoice.status === "draft" && (
                <>
                  <button
                    className="secondary"
                    onClick={() => setEditingInvoiceId(invoice.id)}
                  >
                    Edit
                  </button>
                  <button onClick={() => issue.mutate(invoice)}>Issue</button>
                </>
              )}
              {invoice.status === "issued" &&
                invoice.payment_status !== "paid" && (
                  <button onClick={() => setPayingInvoiceId(invoice.id)}>
                    Record payment
                  </button>
                )}
              {invoice.status === "issued" && invoice.paid_cents === 0 && (
                <button
                  className="danger"
                  onClick={() => voidInvoice.mutate(invoice)}
                >
                  Void
                </button>
              )}
            </div>
            {editingInvoiceId === invoice.id && (
              <form
                className="record-editor"
                action={(values) => editLines.mutate({ invoice, values })}
              >
                <input
                  name="description"
                  defaultValue={invoice.lines[0]?.description ?? "Service"}
                  required
                />
                <input
                  name="quantity"
                  type="number"
                  min="1"
                  defaultValue={invoice.lines[0]?.quantity ?? 1}
                  required
                />
                <input
                  name="price"
                  type="number"
                  min="0"
                  step="0.01"
                  defaultValue={(invoice.lines[0]?.unit_price_cents ?? 0) / 100}
                  required
                />
                <button>Save lines</button>
              </form>
            )}
            {payingInvoiceId === invoice.id && (
              <form
                className="record-editor"
                action={(values) =>
                  payment.mutate({ invoiceId: invoice.id, values })
                }
              >
                <input
                  name="amount"
                  type="number"
                  min="0.01"
                  step="0.01"
                  max={(invoice.total_cents - invoice.paid_cents) / 100}
                  placeholder="Amount"
                  required
                />
                <select name="method" defaultValue="card">
                  <option value="card">Card</option>
                  <option value="cash">Cash</option>
                  <option value="bank">Bank transfer</option>
                </select>
                <button>Save payment</button>
              </form>
            )}
          </article>
        ))}
      </div>
    </main>
  );
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "Not set";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ToolCard({
  tool,
  onRetry,
}: {
  tool: AgentRunDetail["tools"][number];
  onRetry?: () => void;
}) {
  const summary =
    tool.output && typeof tool.output.summary === "string"
      ? tool.output.summary
      : null;
  const links =
    tool.output && Array.isArray(tool.output.links) ? tool.output.links : [];
  return (
    <article className={`tool-card ${tool.status}`}>
      <div className="tool-card-heading">
        <span className="tool-icon">↗</span>
        <div>
          <strong>{tool.name.replaceAll("_", " ")}</strong>
          <small>
            {tool.risk} · v{tool.version}
          </small>
        </div>
        <span className="status-pill">{tool.status.replaceAll("_", " ")}</span>
      </div>
      <dl className="tool-fields">
        {Object.entries(tool.input).map(([name, value]) => (
          <div key={name}>
            <dt>{name.replaceAll("_", " ")}</dt>
            <dd>{displayValue(value)}</dd>
          </div>
        ))}
      </dl>
      {summary && <p className="tool-summary">{summary}</p>}
      {links.map((link, index) => {
        const item = link as { label?: string; resource?: string; id?: string };
        return (
          <span className="resource-link" key={`${item.id}-${index}`}>
            {item.label ?? item.resource ?? "Related record"}
          </span>
        );
      })}
      {tool.error && (
        <div className="error-banner">{displayValue(tool.error.message)}</div>
      )}
      {tool.status === "failed" && onRetry && (
        <button className="secondary" onClick={onRetry}>
          Retry run
        </button>
      )}
    </article>
  );
}

function ApprovalCard({
  approval,
  tool,
  pending,
  onDecision,
}: {
  approval: Approval;
  tool: AgentRunDetail["tools"][number];
  pending: boolean;
  onDecision: (
    decision: "approve" | "reject" | "edit",
    argumentsValue?: Record<string, unknown>,
    reason?: string,
  ) => void;
}) {
  const [argumentsValue, setArgumentsValue] = useState<Record<string, unknown>>(
    { ...approval.proposed_args },
  );
  const [reason, setReason] = useState("");
  return (
    <section className="approval-card" aria-label="Human review required">
      <span className="eyebrow">Human review required</span>
      <h3>Approve {tool.name.replaceAll("_", " ")}?</h3>
      <p>
        This action changes business data. Review every field before it runs.
      </p>
      <div className="approval-fields">
        {Object.entries(argumentsValue).map(([name, value]) => (
          <label key={name}>
            {name.replaceAll("_", " ")}
            <input
              aria-label={`Approval ${name}`}
              value={
                displayValue(value) === "Not set" ? "" : displayValue(value)
              }
              onChange={(event) =>
                setArgumentsValue((current) => ({
                  ...current,
                  [name]:
                    typeof value === "number"
                      ? Number(event.target.value)
                      : event.target.value || null,
                }))
              }
            />
          </label>
        ))}
        <label>
          Review reason
          <input
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Optional audit note"
          />
        </label>
      </div>
      <div className="approval-actions">
        <button
          disabled={pending}
          onClick={() => onDecision("approve", undefined, reason)}
        >
          Approve
        </button>
        <button
          className="secondary"
          disabled={pending}
          onClick={() => onDecision("edit", argumentsValue, reason)}
        >
          Save edits & approve
        </button>
        <button
          className="danger"
          disabled={pending}
          onClick={() => onDecision("reject", undefined, reason)}
        >
          Reject
        </button>
      </div>
    </section>
  );
}

export function ChatMessage({
  message,
  api,
}: {
  message: ConversationMessage;
  api: OmniApiClient;
}) {
  const [speaking, setSpeaking] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const playbackDoneRef = useRef<(() => void) | null>(null);
  const speechRequestRef = useRef(0);

  const releaseAudio = useCallback(() => {
    playbackDoneRef.current?.();
    playbackDoneRef.current = null;
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      audio.onended = null;
      audio.onpause = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  const stopSpeaking = useCallback(() => {
    speechRequestRef.current += 1;
    releaseAudio();
    setSpeaking(false);
  }, [releaseAudio]);

  useEffect(() => {
    window.addEventListener(STOP_SPEECH_EVENT, stopSpeaking);
    return () => {
      window.removeEventListener(STOP_SPEECH_EVENT, stopSpeaking);
      speechRequestRef.current += 1;
      releaseAudio();
    };
  }, [releaseAudio, stopSpeaking]);

  const speak = async () => {
    if (speaking) {
      stopSpeaking();
      return;
    }
    stopActiveSpeech();
    const requestId = speechRequestRef.current + 1;
    speechRequestRef.current = requestId;
    setSpeaking(true);
    setSpeechError(null);
    try {
      const blob = await api.synthesizeSpeech(message.content);
      if (requestId !== speechRequestRef.current) return;
      const url = URL.createObjectURL(blob);
      audioUrlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;
      await audio.play();
      await new Promise<void>((resolve, reject) => {
        playbackDoneRef.current = resolve;
        audio.onended = () => resolve();
        audio.onpause = () => resolve();
        audio.onerror = () => reject(new Error("Audio playback failed"));
      });
    } catch (error) {
      if (requestId === speechRequestRef.current)
        setSpeechError(
          error instanceof Error ? error.message : "Speech playback failed",
        );
    } finally {
      if (requestId === speechRequestRef.current) {
        releaseAudio();
        setSpeaking(false);
      }
    }
  };

  return (
    <article className={`chat-message ${message.role}`}>
      <span>{message.role === "user" ? "You" : "Omni Copilot"}</span>
      <p>{message.content}</p>
      {message.role === "assistant" && (
        <button
          className="speak-message"
          onClick={() => void speak()}
          aria-label={speaking ? "Stop read aloud" : "Read aloud"}
        >
          {speaking ? "■ Stop audio" : "🔊 Read aloud"}
        </button>
      )}
      {speechError && <small className="speech-error">{speechError}</small>}
      {!!message.citations.length && (
        <div className="citations" aria-label="Sources">
          {message.citations.map((citation, index) => {
            const source = citation as {
              id?: string;
              uri?: string;
              title?: string;
            };
            return (
              <a
                key={`${source.id}-${index}`}
                href={source.uri}
                target={source.uri ? "_blank" : undefined}
                rel="noreferrer"
              >
                {source.title ?? "Source"}
              </a>
            );
          })}
        </div>
      )}
    </article>
  );
}

function ExtractionReviewCard({
  extraction,
  pending,
  onReview,
}: {
  extraction: Awaited<ReturnType<OmniApiClient["extractions"]>>[number];
  pending: boolean;
  onReview: (
    decision: "accept" | "correct" | "reject",
    fields?: Record<string, unknown>,
  ) => void;
}) {
  const [fields, setFields] = useState<Record<string, unknown>>({
    ...extraction.extracted_fields,
  });
  return (
    <article className="extraction-card">
      <div>
        <span className="eyebrow">
          {extraction.schema_name.replaceAll("_", " ")}
        </span>
        <h2>{(extraction.confidence_bps / 100).toFixed(0)}% confidence</h2>
        <p>{extraction.input_text}</p>
      </div>
      <div className="approval-fields">
        {Object.entries(fields).map(([name, value]) => (
          <label key={name}>
            {name.replaceAll("_", " ")}
            <input
              value={displayValue(value)}
              onChange={(event) =>
                setFields((current) => ({
                  ...current,
                  [name]: event.target.value,
                }))
              }
            />
          </label>
        ))}
      </div>
      <div className="approval-actions">
        <button disabled={pending} onClick={() => onReview("accept")}>
          Accept
        </button>
        <button
          className="secondary"
          disabled={pending}
          onClick={() => onReview("correct", fields)}
        >
          Save correction
        </button>
        <button
          className="danger"
          disabled={pending}
          onClick={() => onReview("reject")}
        >
          Reject
        </button>
      </div>
    </article>
  );
}

function IntelligencePage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const dashboard = useQuery({
    queryKey: ["intelligence-dashboard", tenantId],
    queryFn: () => api.intelligenceDashboard(),
    refetchInterval: 15_000,
  });
  const results = useQuery({
    queryKey: ["intelligence-calls", tenantId, query],
    queryFn: () => api.intelligenceCalls({ q: query || undefined }),
  });
  const voiceCalls = useQuery({
    queryKey: ["voice-calls", tenantId],
    queryFn: () => api.voiceCalls(),
  });
  const detail = useQuery({
    queryKey: ["intelligence-call", tenantId, selectedId],
    queryFn: () => api.intelligenceCall(selectedId!),
    enabled: Boolean(selectedId),
  });
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({
        queryKey: ["intelligence-dashboard", tenantId],
      }),
      cache.invalidateQueries({ queryKey: ["intelligence-calls", tenantId] }),
      cache.invalidateQueries({ queryKey: ["intelligence-call", tenantId] }),
    ]);
  };
  const processRecent = useMutation({
    mutationFn: async () => {
      const completed =
        voiceCalls.data?.filter((call) => call.ended_at).slice(0, 50) ?? [];
      await Promise.all(
        completed.map((call) => api.processIntelligenceCall(call.id)),
      );
      return completed.length;
    },
    onSuccess: refresh,
  });
  const review = useMutation({
    mutationFn: ({
      callId,
      decision,
    }: {
      callId: string;
      decision: "accept" | "reject";
    }) =>
      api.reviewIntelligence(callId, {
        decision,
        corrected_fields: null,
        reason: `${decision}ed in conversation intelligence review`,
      }),
    onSuccess: refresh,
  });
  const active = detail.data as CallIntelligenceDetail | undefined;
  const data = dashboard.data;
  const percent = (value?: number) => `${Math.round((value ?? 0) * 100)}%`;
  return (
    <main className="intelligence-page">
      <section className="page-heading">
        <div>
          <span className="eyebrow">Conversation intelligence</span>
          <h1>Call intelligence</h1>
          <p>
            Reconciled call facts, searchable redacted transcripts, quality
            signals, and reviewable extraction provenance.
          </p>
        </div>
        <button
          disabled={processRecent.isPending || voiceCalls.isPending}
          onClick={() => processRecent.mutate()}
        >
          {processRecent.isPending ? "Processing…" : "Process recent calls"}
        </button>
      </section>
      <section
        className="automation-metrics"
        aria-label="Call intelligence metrics"
      >
        <Metric label="Modeled calls" value={data?.total_calls ?? 0} />
        <Metric label="Answered" value={percent(data?.answered_rate)} />
        <Metric label="Contained" value={percent(data?.containment_rate)} />
        <Metric label="Transferred" value={percent(data?.transfer_rate)} />
        <Metric label="Reconciled" value={percent(data?.reconciliation_rate)} />
      </section>
      <div className="intelligence-quality-strip">
        <span>
          Pipeline success{" "}
          <strong>{percent(data?.pipeline_success_rate)}</strong>
        </span>
        <span>
          Review queue <strong>{data?.review_pending ?? 0}</strong>
        </span>
        <span>
          Compliance flags <strong>{data?.compliance_flagged ?? 0}</strong>
        </span>
        <span>
          Freshness{" "}
          <strong>
            {data?.data_freshness_at
              ? new Date(data.data_freshness_at).toLocaleString()
              : "No data"}
          </strong>
        </span>
      </div>
      <section className="intelligence-layout">
        <div className="intelligence-column">
          <article className="intelligence-chart-card">
            <h2>Reasons and outcomes</h2>
            <div className="intelligence-bars">
              {data?.topics.map((item) => (
                <div key={String(item.key)}>
                  <span>{String(item.key).replaceAll("_", " ")}</span>
                  <progress
                    value={Number(item.count)}
                    max={Math.max(1, data.total_calls)}
                  />
                  <strong>{Number(item.count)}</strong>
                </div>
              ))}
            </div>
          </article>
          <label className="intelligence-search">
            Search redacted calls
            <input
              aria-label="Search call intelligence"
              placeholder="Topic, intent, outcome, transcript…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          {results.isPending && (
            <div className="panel">Loading intelligence…</div>
          )}
          {results.isError && (
            <div className="error-banner">{results.error.message}</div>
          )}
          {results.data?.length === 0 && (
            <div className="empty">
              <h2>No modeled calls</h2>
              <p>Process completed calls to populate the governed mart.</p>
            </div>
          )}
          {results.data?.map((item) => (
            <button
              className={`intelligence-result ${selectedId === item.call_id ? "selected" : ""}`}
              key={item.call_id}
              onClick={() => setSelectedId(item.call_id)}
            >
              <span>
                <strong>{item.topic.replaceAll("_", " ")}</strong>
                <small>{new Date(item.started_at).toLocaleString()}</small>
              </span>
              <span>{item.summary}</span>
              <span className={`status-pill ${item.status}`}>
                {item.status}
              </span>
            </button>
          ))}
        </div>
        <aside className="intelligence-inspector">
          {!active && (
            <>
              <span className="eyebrow">Traceable evidence</span>
              <h2>Select a modeled call</h2>
              <p>
                Inspect summaries, confidence, revisions, lineage, and
                reconciliation.
              </p>
            </>
          )}
          {active && (
            <>
              <div className="card-title-row">
                <h2>{active.intelligence.topic.replaceAll("_", " ")}</h2>
                <span className={`status-pill ${active.intelligence.status}`}>
                  {active.intelligence.status}
                </span>
              </div>
              <p>{active.intelligence.summary}</p>
              <dl className="intelligence-facts">
                <div>
                  <dt>Intent</dt>
                  <dd>{active.intelligence.intent}</dd>
                </div>
                <div>
                  <dt>Outcome</dt>
                  <dd>{active.intelligence.outcome}</dd>
                </div>
                <div>
                  <dt>Confidence</dt>
                  <dd>
                    {percent(active.intelligence.confidence_bps / 10_000)}
                  </dd>
                </div>
                <div>
                  <dt>Duration</dt>
                  <dd>{Math.round(active.fact.duration_ms / 1000)} sec</dd>
                </div>
                <div>
                  <dt>Revision</dt>
                  <dd>v{active.transcript.version}</dd>
                </div>
                <div>
                  <dt>Extractor</dt>
                  <dd>{active.intelligence.extractor_version}</dd>
                </div>
              </dl>
              {active.intelligence.compliance_flags.length > 0 && (
                <div className="error-banner">
                  Compliance:{" "}
                  {active.intelligence.compliance_flags
                    .map((flag) =>
                      String((flag as { code?: unknown }).code ?? "unknown"),
                    )
                    .join(", ")}
                </div>
              )}
              {active.intelligence.status === "pending_review" && (
                <div className="review-callout">
                  <strong>Human quality review required</strong>
                  <div className="button-row">
                    <button
                      onClick={() =>
                        review.mutate({
                          callId: active.intelligence.call_id,
                          decision: "accept",
                        })
                      }
                    >
                      Accept extraction
                    </button>
                    <button
                      className="secondary"
                      onClick={() =>
                        review.mutate({
                          callId: active.intelligence.call_id,
                          decision: "reject",
                        })
                      }
                    >
                      Reject
                    </button>
                  </div>
                </div>
              )}
              <h3>Redacted transcript</h3>
              <div className="intelligence-transcript">
                {active.transcript.segments.map((segment) => (
                  <p
                    key={`${String((segment as { sequence?: unknown }).sequence)}-${String((segment as { start_ms?: unknown }).start_ms)}`}
                  >
                    <strong>
                      {String((segment as { speaker?: unknown }).speaker)}
                    </strong>
                    {String((segment as { text?: unknown }).text)}
                  </p>
                ))}
              </div>
              <details>
                <summary>Pipeline lineage</summary>
                <p>
                  {active.pipeline.pipeline_version} ·{" "}
                  {active.pipeline.checkpoints.join(" → ")}
                </p>
                <p>
                  {active.reconciliations.every((item) => item.complete)
                    ? "Provider records reconciled"
                    : "Reconciliation discrepancy requires attention"}
                </p>
              </details>
            </>
          )}
        </aside>
      </section>
    </main>
  );
}

function VoicePage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [selectedCallId, setSelectedCallId] = useState<string | null>(null);
  const [caller, setCaller] = useState("+15550100");
  const [callerName, setCallerName] = useState("Jamie Caller");
  const [reason, setReason] = useState("My heating system stopped working");
  const [reviewTitle, setReviewTitle] = useState("");
  const config = useQuery({
    queryKey: ["voice-config", tenantId],
    queryFn: () => api.voiceConfig(),
  });
  const calls = useQuery({
    queryKey: ["voice-calls", tenantId],
    queryFn: () => api.voiceCalls(),
    refetchInterval: 10_000,
  });
  const metrics = useQuery({
    queryKey: ["voice-metrics", tenantId],
    queryFn: () => api.voiceMetrics(),
    refetchInterval: 10_000,
  });
  const detail = useQuery({
    queryKey: ["voice-call", tenantId, selectedCallId],
    queryFn: () => api.voiceCall(selectedCallId!),
    enabled: Boolean(selectedCallId),
  });
  const refresh = async (call?: VoiceCall) => {
    if (call) setSelectedCallId(call.id);
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["voice-calls", tenantId] }),
      cache.invalidateQueries({ queryKey: ["voice-metrics", tenantId] }),
      cache.invalidateQueries({ queryKey: ["voice-call", tenantId] }),
    ]);
  };
  const saveConfig = useMutation({
    mutationFn: (enabled: boolean) => {
      const current = config.data!;
      return api.updateVoiceConfig({
        enabled,
        pilot_mode: current.pilot_mode,
        allowed_hours: current.allowed_hours,
        max_concurrent_calls: current.max_concurrent_calls,
        allowed_regions: current.allowed_regions,
        disclosure_text: current.disclosure_text,
        require_ai_consent: current.require_ai_consent,
        require_recording_consent: current.require_recording_consent,
        retention_days: current.retention_days,
        transfer_number: current.transfer_number,
        emergency_keywords: current.emergency_keywords,
        allowed_tools: current.allowed_tools,
        prompt_version: current.prompt_version,
      });
    },
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["voice-config", tenantId] }),
  });
  const simulate = useMutation({
    mutationFn: () =>
      api.simulateVoiceCall({
        caller,
        callee: "+15550999",
        region: "local",
        turns: [
          {
            type: "transcript",
            text: "yes",
            confidence_bps: 9900,
            duration_ms: 400,
            heard_response_boundary_ms: 0,
            digit: null,
          },
          {
            type: "transcript",
            text: callerName,
            confidence_bps: 9800,
            duration_ms: 600,
            heard_response_boundary_ms: 0,
            digit: null,
          },
          {
            type: "transcript",
            text: reason,
            confidence_bps: 9600,
            duration_ms: 1000,
            heard_response_boundary_ms: 0,
            digit: null,
          },
          {
            type: "transcript",
            text: "yes",
            confidence_bps: 9900,
            duration_ms: 400,
            heard_response_boundary_ms: 0,
            digit: null,
          },
        ],
      }),
    onSuccess: (call) => refresh(call),
  });
  const review = useMutation({
    mutationFn: ({
      call,
      decision,
    }: {
      call: VoiceCall;
      decision: "approve" | "reject";
    }) => {
      const args = call.review?.proposed_args;
      return api.reviewVoiceCall(call.id, {
        decision,
        args:
          decision === "approve" && args
            ? { ...args, title: reviewTitle || args.title }
            : null,
        reason:
          decision === "approve"
            ? "Operator reviewed voice intake"
            : "Operator rejected voice intake",
      });
    },
    onSuccess: (call) => refresh(call),
  });
  const transfer = useMutation({
    mutationFn: (callId: string) =>
      api.transferVoiceCall(callId, "Operator requested transfer"),
    onSuccess: (call) => refresh(call),
  });
  const openRecording = async (callId: string) => {
    const blob = await api.voiceRecording(callId);
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener,noreferrer");
    window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
  };
  const activeCall = detail.data;
  const firstAudio = activeCall?.latency_metrics
    .speech_end_to_first_audio_ms as number[] | undefined;
  return (
    <main className="voice-page">
      <section className="page-heading">
        <div>
          <span className="eyebrow">After-hours intake</span>
          <h1>Voice pilot</h1>
          <p>
            Limited cohort, explicit consent, human fallback, and reviewed
            actions.
          </p>
        </div>
        {config.data && (
          <button
            className={config.data.enabled ? "danger-link" : ""}
            disabled={saveConfig.isPending}
            onClick={() => saveConfig.mutate(!config.data.enabled)}
          >
            {config.data.enabled ? "Disable pilot" : "Enable internal pilot"}
          </button>
        )}
      </section>
      <section className="automation-metrics" aria-label="Voice pilot metrics">
        <Metric label="Calls" value={metrics.data?.total_calls ?? 0} />
        <Metric label="Active" value={metrics.data?.active_calls ?? 0} />
        <Metric label="Transfers" value={metrics.data?.transferred ?? 0} />
        <Metric
          label="Awaiting review"
          value={metrics.data?.review_pending ?? 0}
        />
        <Metric
          label="Avg first audio ms"
          value={metrics.data?.average_first_audio_ms ?? 0}
        />
      </section>
      <section className="voice-layout">
        <div className="voice-column">
          <article className="voice-policy-card">
            <div className="card-title-row">
              <h2>Pilot policy</h2>
              <span
                className={`status-pill ${config.data?.enabled ? "active" : ""}`}
              >
                {config.data?.enabled ? "enabled" : "disabled"}
              </span>
            </div>
            <p>{config.data?.disclosure_text}</p>
            <dl>
              <div>
                <dt>Mode</dt>
                <dd>{config.data?.pilot_mode}</dd>
              </div>
              <div>
                <dt>Concurrency</dt>
                <dd>{config.data?.max_concurrent_calls ?? 0}</dd>
              </div>
              <div>
                <dt>Retention</dt>
                <dd>{config.data?.retention_days ?? 0} days</dd>
              </div>
              <div>
                <dt>Tools</dt>
                <dd>{config.data?.allowed_tools?.join(", ")}</dd>
              </div>
            </dl>
          </article>
          <article className="voice-policy-card">
            <span className="eyebrow">Safe simulator</span>
            <h2>Test an intake</h2>
            <div className="voice-simulator">
              <label>
                Caller
                <input
                  value={caller}
                  onChange={(event) => setCaller(event.target.value)}
                />
              </label>
              <label>
                Name
                <input
                  value={callerName}
                  onChange={(event) => setCallerName(event.target.value)}
                />
              </label>
              <label>
                Reason
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                />
              </label>
              <button
                disabled={!config.data?.enabled || simulate.isPending}
                onClick={() => simulate.mutate()}
              >
                Simulate call
              </button>
            </div>
            {simulate.error && (
              <div className="error-banner">{simulate.error.message}</div>
            )}
          </article>
          <div className="section-title">
            <div>
              <span className="eyebrow">Call operations</span>
              <h2>Recent calls</h2>
            </div>
          </div>
          {calls.data?.map((call) => (
            <button
              className={`call-row ${call.id === selectedCallId ? "selected" : ""}`}
              key={call.id}
              onClick={() => setSelectedCallId(call.id)}
            >
              <span>
                <strong>{call.caller}</strong>
                <small>{new Date(call.started_at).toLocaleString()}</small>
              </span>
              <span className={`status-pill ${call.status}`}>
                {call.status}
              </span>
              <span>{call.outcome ?? "In progress"}</span>
            </button>
          ))}
        </div>
        <aside className="call-inspector">
          {!activeCall && (
            <>
              <span className="eyebrow">Call inspector</span>
              <h2>Select a call</h2>
              <p>Review consent, transcript, tool use, latency, and outcome.</p>
            </>
          )}
          {activeCall && (
            <>
              <div className="card-title-row">
                <h2>{activeCall.caller}</h2>
                <span className={`status-pill ${activeCall.status}`}>
                  {activeCall.status}
                </span>
              </div>
              <p>
                {activeCall.outcome ?? "Call underway"} · consent{" "}
                {activeCall.consent_status} · recording{" "}
                {activeCall.recording_status}
              </p>
              <div className="latency-strip">
                <span>First audio</span>
                <strong>{firstAudio?.at(-1) ?? 0} ms</strong>
                <small>
                  target ≤ {metrics.data?.target_first_audio_ms ?? 800} ms
                </small>
              </div>
              {activeCall.review?.status === "pending" && (
                <div className="review-callout">
                  <strong>Callback needs review</strong>
                  <label>
                    Job title
                    <input
                      value={
                        reviewTitle ||
                        String(activeCall.review.proposed_args.title ?? "")
                      }
                      onChange={(event) => setReviewTitle(event.target.value)}
                    />
                  </label>
                  <div className="button-row">
                    <button
                      onClick={() =>
                        review.mutate({ call: activeCall, decision: "approve" })
                      }
                    >
                      Approve & create draft
                    </button>
                    <button
                      className="secondary"
                      onClick={() =>
                        review.mutate({ call: activeCall, decision: "reject" })
                      }
                    >
                      Reject
                    </button>
                  </div>
                </div>
              )}
              <div className="button-row">
                <button
                  className="secondary"
                  onClick={() => transfer.mutate(activeCall.id)}
                >
                  Transfer to human
                </button>
                {activeCall.recording && (
                  <button
                    className="secondary"
                    onClick={() => void openRecording(activeCall.id)}
                  >
                    Open recording
                  </button>
                )}
              </div>
              <h3>Transcript</h3>
              <div className="voice-transcript">
                {activeCall.transcript?.map((segment) => (
                  <p className={segment.speaker} key={segment.id}>
                    <strong>{segment.speaker}</strong>
                    {segment.text}
                    <small>
                      {segment.start_ms}–{segment.end_ms} ms
                    </small>
                  </p>
                ))}
              </div>
              <details>
                <summary>
                  Immutable event timeline ({activeCall.events?.length ?? 0})
                </summary>
                <ol className="event-timeline">
                  {activeCall.events?.map((event) => (
                    <li key={event.id}>
                      <strong>
                        {event.sequence}. {event.event_type}
                      </strong>
                      <small>
                        {new Date(event.occurred_at).toLocaleTimeString()}
                      </small>
                    </li>
                  ))}
                </ol>
              </details>
            </>
          )}
        </aside>
      </section>
    </main>
  );
}

function AutomationsPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [editing, setEditing] = useState<AutomationDefinition | null>(null);
  const [description, setDescription] = useState("");
  const [triggerType, setTriggerType] = useState("");
  const [conditions, setConditions] = useState("[]");
  const [steps, setSteps] = useState("[]");
  const [approvalMode, setApprovalMode] = useState<
    "always" | "never" | "external" | "writes"
  >("writes");
  const [testPayload, setTestPayload] = useState("{}");
  const [builderError, setBuilderError] = useState("");
  const templates = useQuery({
    queryKey: ["automation-templates", tenantId],
    queryFn: () => api.automationTemplates(),
  });
  const definitions = useQuery({
    queryKey: ["automations", tenantId],
    queryFn: () => api.automations(),
  });
  const runs = useQuery({
    queryKey: ["automation-runs", tenantId],
    queryFn: () => api.automationRuns(),
    refetchInterval: 10_000,
  });
  const metrics = useQuery({
    queryKey: ["automation-metrics", tenantId],
    queryFn: () => api.automationMetrics(),
  });
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["automations", tenantId] }),
      cache.invalidateQueries({ queryKey: ["automation-runs", tenantId] }),
      cache.invalidateQueries({ queryKey: ["automation-metrics", tenantId] }),
    ]);
  };
  const install = useMutation({
    mutationFn: (key: string) => api.installAutomationTemplate(key),
    onSuccess: refresh,
  });
  const control = useMutation({
    mutationFn: async (command: {
      kind: "activate" | "pause" | "kill" | "replay" | "retry" | "compensate";
      id: string;
      mode?: "shadow" | "production";
    }) => {
      if (command.kind === "activate")
        return api.activateAutomation(command.id, command.mode ?? "shadow");
      if (command.kind === "pause") return api.pauseAutomation(command.id);
      if (command.kind === "kill")
        return api.killAutomation(
          command.id,
          "Stopped from automation console",
        );
      if (command.kind === "replay") return api.replayAutomationRun(command.id);
      if (command.kind === "retry") return api.retryAutomationRun(command.id);
      return api.compensateAutomationRun(command.id);
    },
    onSuccess: refresh,
  });
  const save = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("Select an automation");
      const parsedConditions = JSON.parse(conditions) as never[];
      const parsedSteps = JSON.parse(steps) as never[];
      return api.updateAutomation(editing.id, {
        description,
        mode: editing.mode as "shadow" | "production",
        trigger_type: triggerType,
        trigger_config: editing.version.trigger_config,
        conditions: parsedConditions,
        steps: parsedSteps,
        approval_rule: { mode: approvalMode },
        rate_limit_per_hour: editing.version.rate_limit_per_hour,
        max_attempts: editing.version.max_attempts,
      });
    },
    onSuccess: async (definition) => {
      setEditing(definition);
      setBuilderError("");
      await refresh();
    },
    onError: (error) => setBuilderError(error.message),
  });
  const test = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("Select an automation");
      return api.testAutomation(
        editing.id,
        JSON.parse(testPayload) as Record<string, unknown>,
      );
    },
    onSuccess: refresh,
    onError: (error) => setBuilderError(error.message),
  });
  const review = useMutation({
    mutationFn: ({
      approvalId,
      decision,
    }: {
      approvalId: string;
      decision: "approve" | "reject";
    }) =>
      api.decideAutomationApproval(approvalId, {
        decision,
        actions: null,
        reason:
          decision === "approve"
            ? "Approved in console"
            : "Rejected in console",
      }),
    onSuccess: refresh,
  });
  const openBuilder = (definition: AutomationDefinition) => {
    setEditing(definition);
    setDescription(definition.description);
    setTriggerType(definition.version.trigger_type);
    setConditions(JSON.stringify(definition.version.conditions, null, 2));
    setSteps(JSON.stringify(definition.version.steps, null, 2));
    setApprovalMode(
      (definition.version.approval_rule.mode as typeof approvalMode) ??
        "writes",
    );
    setBuilderError("");
  };
  const installedKeys = new Set(
    definitions.data?.map((definition) => definition.template_key) ?? [],
  );
  return (
    <main className="automation-page">
      <section className="page-heading">
        <div>
          <span className="eyebrow">Durable workflows</span>
          <h1>Automations</h1>
          <p>
            Start in shadow mode, inspect every decision, then promote safely.
          </p>
        </div>
      </section>
      <section className="automation-metrics" aria-label="Automation metrics">
        <Metric label="Total runs" value={metrics.data?.total ?? 0} />
        <Metric label="Successful" value={metrics.data?.succeeded ?? 0} />
        <Metric label="Shadow" value={metrics.data?.shadowed ?? 0} />
        <Metric
          label="Awaiting review"
          value={metrics.data?.waiting_approval ?? 0}
        />
        <Metric label="Dead letter" value={metrics.data?.dead_letter ?? 0} />
      </section>
      <section className="automation-layout">
        <div className="automation-column">
          <div className="section-title">
            <div>
              <span className="eyebrow">Template library</span>
              <h2>Proven starting points</h2>
            </div>
          </div>
          <div className="template-grid">
            {templates.data?.map((template) => (
              <article className="automation-card" key={template.key}>
                <span className="status-pill">{template.trigger_type}</span>
                <h3>{template.name}</h3>
                <p>{template.description}</p>
                <button
                  className="secondary"
                  disabled={
                    installedKeys.has(template.key) || install.isPending
                  }
                  onClick={() => install.mutate(template.key)}
                >
                  {installedKeys.has(template.key)
                    ? "Installed"
                    : "Install in shadow"}
                </button>
              </article>
            ))}
          </div>
          <div className="section-title">
            <div>
              <span className="eyebrow">Control plane</span>
              <h2>Installed automations</h2>
            </div>
          </div>
          {definitions.data?.map((definition) => (
            <article className="automation-card installed" key={definition.id}>
              <div>
                <div className="card-title-row">
                  <h3>{definition.name}</h3>
                  <span className={`status-pill ${definition.status}`}>
                    {definition.status}
                  </span>
                  <span className="status-pill">
                    {definition.mode} · v{definition.current_version}
                  </span>
                </div>
                <p>{definition.description}</p>
                <small>
                  When {definition.version.trigger_type} ·{" "}
                  {definition.version.steps.length} step(s)
                </small>
              </div>
              <div className="button-row">
                <button
                  className="secondary"
                  onClick={() => openBuilder(definition)}
                >
                  Edit & test
                </button>
                {definition.status !== "active" &&
                  definition.status !== "killed" && (
                    <button
                      onClick={() =>
                        control.mutate({
                          kind: "activate",
                          id: definition.id,
                          mode: "shadow",
                        })
                      }
                    >
                      Run in shadow
                    </button>
                  )}
                {definition.status === "active" &&
                  definition.mode === "shadow" && (
                    <button
                      onClick={() =>
                        control.mutate({
                          kind: "activate",
                          id: definition.id,
                          mode: "production",
                        })
                      }
                    >
                      Promote
                    </button>
                  )}
                {definition.status === "active" && (
                  <button
                    className="secondary"
                    onClick={() =>
                      control.mutate({ kind: "pause", id: definition.id })
                    }
                  >
                    Pause
                  </button>
                )}
                {definition.status !== "killed" && (
                  <button
                    className="danger-link"
                    onClick={() =>
                      control.mutate({ kind: "kill", id: definition.id })
                    }
                  >
                    Kill
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
        <aside className="automation-builder">
          <span className="eyebrow">Versioned builder</span>
          <h2>{editing?.name ?? "Select an automation"}</h2>
          {editing ? (
            <>
              <label>
                Description
                <textarea
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                />
              </label>
              <label>
                Trigger
                <input
                  value={triggerType}
                  onChange={(event) => setTriggerType(event.target.value)}
                />
              </label>
              <label>
                Conditions (JSON)
                <textarea
                  className="code-input"
                  value={conditions}
                  onChange={(event) => setConditions(event.target.value)}
                />
              </label>
              <label>
                AI/extraction/actions (JSON)
                <textarea
                  className="code-input tall"
                  value={steps}
                  onChange={(event) => setSteps(event.target.value)}
                />
              </label>
              <label>
                Approval policy
                <select
                  value={approvalMode}
                  onChange={(event) =>
                    setApprovalMode(event.target.value as typeof approvalMode)
                  }
                >
                  <option value="writes">All writes</option>
                  <option value="external">External actions</option>
                  <option value="always">Always</option>
                  <option value="never">Never</option>
                </select>
              </label>
              <button onClick={() => save.mutate()} disabled={save.isPending}>
                Save as v{editing.current_version + 1}
              </button>
              <hr />
              <label>
                Test event payload (JSON)
                <textarea
                  className="code-input"
                  value={testPayload}
                  onChange={(event) => setTestPayload(event.target.value)}
                />
              </label>
              <button
                className="secondary"
                onClick={() => test.mutate()}
                disabled={test.isPending}
              >
                Run without side effects
              </button>
              {builderError && (
                <div className="error-banner">{builderError}</div>
              )}
            </>
          ) : (
            <p>
              Choose an installed workflow to edit conditions, steps, approval
              policy, and test data.
            </p>
          )}
        </aside>
      </section>
      <section className="run-history">
        <div className="section-title">
          <div>
            <span className="eyebrow">Explainable history</span>
            <h2>Recent runs</h2>
          </div>
        </div>
        {runs.data?.length === 0 && <p>No automation runs yet.</p>}
        {runs.data?.map((run: AutomationRun) => (
          <article className="run-card" key={run.id}>
            <div className="card-title-row">
              <h3>{run.definition_name}</h3>
              <span className={`status-pill ${run.status}`}>{run.status}</span>
              <span className="status-pill">
                v{run.version} · {run.mode}
              </span>
            </div>
            <p>{run.reason}</p>
            <small>
              Attempt {run.attempt}/{run.max_attempts} ·{" "}
              {run.changed_resources.length} record(s) changed
            </small>
            {run.approval?.status === "pending" && (
              <div className="review-callout">
                <strong>Human review required</strong>
                <p>
                  {run.approval.proposed_actions.length} action(s) are paused.
                </p>
                <div className="button-row">
                  <button
                    onClick={() =>
                      review.mutate({
                        approvalId: run.approval!.id,
                        decision: "approve",
                      })
                    }
                  >
                    Approve
                  </button>
                  <button
                    className="secondary"
                    onClick={() =>
                      review.mutate({
                        approvalId: run.approval!.id,
                        decision: "reject",
                      })
                    }
                  >
                    Reject
                  </button>
                </div>
              </div>
            )}
            <div className="button-row">
              <button
                className="secondary"
                onClick={() => control.mutate({ kind: "replay", id: run.id })}
              >
                Replay same version
              </button>
              {(run.status === "retrying" || run.status === "dead_letter") && (
                <button
                  onClick={() => control.mutate({ kind: "retry", id: run.id })}
                >
                  Retry
                </button>
              )}
              {run.status === "succeeded" &&
                run.changed_resources.some(
                  (item) => (item as { reversible?: boolean }).reversible,
                ) && (
                  <button
                    className="danger-link"
                    onClick={() =>
                      control.mutate({ kind: "compensate", id: run.id })
                    }
                  >
                    Compensate
                  </button>
                )}
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function AssistantPage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const cache = useQueryClient();
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activeRun, setActiveRun] = useState<AgentRunDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [pendingPrompt, setPendingPrompt] = useState<string | null>(null);
  const [mode, setMode] = useState<"chat" | "knowledge" | "review">("chat");
  const [knowledgeTitle, setKnowledgeTitle] = useState("");
  const [knowledgeContent, setKnowledgeContent] = useState("");
  const [extractionText, setExtractionText] = useState("");
  const [prompt, setPrompt] = useState("");
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recordingStreamRef = useRef<MediaStream | null>(null);
  const conversations = useQuery({
    queryKey: ["ai-conversations", tenantId],
    queryFn: () => api.conversations(),
  });
  const selectedConversationId =
    conversationId ?? conversations.data?.[0]?.id ?? null;
  const messages = useQuery({
    queryKey: ["ai-messages", tenantId, selectedConversationId],
    queryFn: () => api.conversationMessages(selectedConversationId!),
    enabled: Boolean(selectedConversationId),
  });
  const knowledge = useQuery({
    queryKey: ["ai-knowledge", tenantId],
    queryFn: () => api.knowledgeDocuments(),
  });
  const extractions = useQuery({
    queryKey: ["ai-extractions", tenantId, "pending_review"],
    queryFn: () => api.extractions("pending_review"),
  });

  const streamRun = async (detail: AgentRunDetail, after = 0) => {
    setActiveRun(detail);
    if (!after) setEvents([]);
    await api.streamAgentRun(detail.run.id, after, (event) =>
      setEvents((current) =>
        current.some((item) => item.sequence === event.sequence)
          ? current
          : [...current, event],
      ),
    );
    await cache.invalidateQueries({
      queryKey: ["ai-messages", tenantId, detail.run.conversation_id],
    });
    await cache.invalidateQueries({ queryKey: ["ai-conversations", tenantId] });
  };

  const run = useMutation({
    mutationFn: async (content: string) => {
      let selected = selectedConversationId;
      if (!selected) {
        const conversation = await api.createConversation();
        selected = conversation.id;
        setConversationId(selected);
      }
      const detail = await api.createAgentRun(selected, content);
      await streamRun(detail);
      return detail;
    },
    onSettled: () => setPendingPrompt(null),
  });
  const review = useMutation({
    mutationFn: async ({
      approvalId,
      decision,
      argumentsValue,
      reason,
    }: {
      approvalId: string;
      decision: "approve" | "reject" | "edit";
      argumentsValue?: Record<string, unknown>;
      reason?: string;
    }) => {
      const after = activeRun?.run.last_sequence ?? 0;
      const detail = await api.decideApproval(approvalId, {
        decision,
        arguments: argumentsValue,
        reason: reason || null,
      });
      await streamRun(detail, after);
      return detail;
    },
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const detail = await api.cancelAgentRun(activeRun!.run.id);
      setActiveRun(detail);
      return detail;
    },
  });
  const regenerate = useMutation({
    mutationFn: async () => {
      const detail = await api.regenerateAgentRun(activeRun!.run.id);
      await streamRun(detail);
      return detail;
    },
  });
  const feedback = useMutation({
    mutationFn: (rating: "up" | "down") =>
      api.addAgentFeedback(activeRun!.run.id, {
        rating,
        category: rating === "up" ? "helpful" : "incorrect",
      }),
  });
  const ingest = useMutation({
    mutationFn: () =>
      api.ingestKnowledge({
        title: knowledgeTitle,
        content: knowledgeContent,
        access_roles: [],
      }),
    onSuccess: async () => {
      setKnowledgeTitle("");
      setKnowledgeContent("");
      await cache.invalidateQueries({ queryKey: ["ai-knowledge", tenantId] });
    },
  });
  const createConversation = useMutation({
    mutationFn: () => api.createConversation(),
    onSuccess: async (conversation) => {
      setConversationId(conversation.id);
      setActiveRun(null);
      setEvents([]);
      await cache.invalidateQueries({
        queryKey: ["ai-conversations", tenantId],
      });
    },
  });
  const extract = useMutation({
    mutationFn: () =>
      api.createExtraction({
        schema_name: "lead_intake",
        input_text: extractionText,
      }),
    onSuccess: async () => {
      setExtractionText("");
      await cache.invalidateQueries({
        queryKey: ["ai-extractions", tenantId],
      });
    },
  });
  const reviewExtraction = useMutation({
    mutationFn: ({
      id,
      decision,
      fields,
    }: {
      id: string;
      decision: "accept" | "correct" | "reject";
      fields?: Record<string, unknown>;
    }) =>
      api.reviewExtraction(id, {
        decision,
        corrected_fields: fields,
        reason: "Reviewed in Omni Copilot",
      }),
    onSuccess: () =>
      cache.invalidateQueries({ queryKey: ["ai-extractions", tenantId] }),
  });

  useEffect(
    () => () => {
      if (recorderRef.current?.state === "recording")
        recorderRef.current.stop();
      recordingStreamRef.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  const startRecording = async () => {
    setAudioError(null);
    stopActiveSpeech();
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    ) {
      setAudioError("This browser does not support microphone recording.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordingStreamRef.current = stream;
      const preferredType = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/ogg;codecs=opus",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(
        stream,
        preferredType ? { mimeType: preferredType } : undefined,
      );
      const chunks: Blob[] = [];
      recorderRef.current = recorder;
      recorder.onerror = () => {
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        setRecording(false);
        setAudioError(
          "Recording failed. Check the microphone permission and try again.",
        );
      };
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        recordingStreamRef.current = null;
        setRecording(false);
        if (!chunks.length) {
          setAudioError("No audio was captured. Please try again.");
          return;
        }
        setTranscribing(true);
        try {
          const result = await api.transcribeAudio(
            new Blob(chunks, { type: recorder.mimeType || "audio/webm" }),
          );
          setPrompt((current) => `${current} ${result.text}`.trim());
        } catch (error) {
          setAudioError(
            error instanceof Error ? error.message : "Transcription failed",
          );
        } finally {
          setTranscribing(false);
        }
      };
      recorder.start(250);
      setRecording(true);
    } catch (error) {
      setAudioError(microphoneErrorMessage(error));
    }
  };

  const stopRecording = () => {
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
  };

  const streamedText = events
    .filter((event) => event.event_type === "text_delta")
    .map((event) => String(event.payload.delta ?? ""))
    .join("");
  const pendingApproval = activeRun?.approvals.find(
    (approval) => approval.status === "pending",
  );
  const pendingTool = activeRun?.tools.find(
    (tool) => tool.id === pendingApproval?.tool_invocation_id,
  );
  const error = run.error ?? review.error ?? ingest.error ?? regenerate.error;

  return (
    <main className="assistant-page">
      <aside className="conversation-rail">
        <button
          onClick={() => createConversation.mutate()}
          disabled={createConversation.isPending}
        >
          + New conversation
        </button>
        <div className="conversation-list">
          {conversations.data?.map((conversation) => (
            <button
              key={conversation.id}
              className={
                conversation.id === selectedConversationId ? "selected" : ""
              }
              onClick={() => {
                setConversationId(conversation.id);
                setActiveRun(null);
                setEvents([]);
              }}
            >
              {conversation.title}
              <small>
                {new Date(conversation.updated_at).toLocaleDateString()}
              </small>
            </button>
          ))}
        </div>
        <button
          className="secondary"
          onClick={() =>
            setMode((value) => (value === "knowledge" ? "chat" : "knowledge"))
          }
        >
          {mode === "knowledge" ? "Close knowledge" : "Manage knowledge"}
        </button>
        <button
          className="secondary"
          onClick={() =>
            setMode((value) => (value === "review" ? "chat" : "review"))
          }
        >
          {mode === "review"
            ? "Close review queue"
            : `Review queue (${extractions.data?.length ?? 0})`}
        </button>
      </aside>
      <section className="copilot-workspace">
        <header className="assistant-heading">
          <div>
            <span className="eyebrow">Omni Copilot</span>
            <h1>Ask, review, then act.</h1>
          </div>
          {activeRun && (
            <div className="run-meter" title="Run cost and latency context">
              <span>{activeRun.run.status.replaceAll("_", " ")}</span>
              <small>
                {activeRun.run.model} ·{" "}
                {activeRun.run.input_tokens + activeRun.run.output_tokens}{" "}
                tokens · ${(activeRun.run.cost_micros / 1_000_000).toFixed(4)}
              </small>
            </div>
          )}
        </header>
        {mode === "knowledge" ? (
          <section className="knowledge-panel">
            <div>
              <h2>Authorized knowledge</h2>
              <p>
                Indexed text is treated as untrusted evidence and is always
                cited.
              </p>
              <div className="knowledge-list">
                {knowledge.data?.map((document) => (
                  <article key={document.id}>
                    <strong>{document.title}</strong>
                    <span>Indexed · v{document.version}</span>
                  </article>
                ))}
              </div>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                ingest.mutate();
              }}
            >
              <label>
                Document title
                <input
                  value={knowledgeTitle}
                  onChange={(event) => setKnowledgeTitle(event.target.value)}
                  required
                />
              </label>
              <label>
                Policy or knowledge text
                <textarea
                  value={knowledgeContent}
                  onChange={(event) => setKnowledgeContent(event.target.value)}
                  rows={8}
                  required
                />
              </label>
              <button disabled={ingest.isPending}>Index knowledge</button>
            </form>
          </section>
        ) : mode === "review" ? (
          <section className="review-panel">
            <div>
              <span className="eyebrow">Human correction queue</span>
              <h2>Classification & extraction review</h2>
              <p>
                Low-confidence structured results wait here. Corrections retain
                their original fields and provenance.
              </p>
            </div>
            <form
              className="stack-form"
              onSubmit={(event) => {
                event.preventDefault();
                extract.mutate();
              }}
            >
              <label>
                Intake text to extract
                <textarea
                  value={extractionText}
                  onChange={(event) => setExtractionText(event.target.value)}
                  placeholder="This is Casey. The kitchen tap is leaking…"
                  rows={3}
                  required
                />
              </label>
              <button disabled={extract.isPending}>Extract lead intake</button>
            </form>
            {!extractions.data?.length && (
              <div className="empty">No items require review.</div>
            )}
            {extractions.data?.map((extraction) => (
              <ExtractionReviewCard
                key={extraction.id}
                extraction={extraction}
                pending={reviewExtraction.isPending}
                onReview={(decision, fields) =>
                  reviewExtraction.mutate({
                    id: extraction.id,
                    decision,
                    fields,
                  })
                }
              />
            ))}
          </section>
        ) : (
          <>
            <section className="message-thread" aria-live="polite">
              {!messages.data?.length && !pendingPrompt && (
                <div className="assistant-empty">
                  <span>✦</span>
                  <h2>What should we work on?</h2>
                  <p>
                    Try “Find customer Northwind”, “What is our cancellation
                    policy?”, or propose a job change.
                  </p>
                </div>
              )}
              {messages.data?.map((message) => (
                <ChatMessage key={message.id} message={message} api={api} />
              ))}
              {pendingPrompt && (
                <article className="chat-message user">
                  <span>You</span>
                  <p>{pendingPrompt}</p>
                </article>
              )}
              {streamedText && pendingPrompt && (
                <article className="chat-message assistant streaming">
                  <span>Omni Copilot</span>
                  <p>{streamedText}</p>
                </article>
              )}
              {activeRun?.tools.map((tool) => (
                <ToolCard
                  key={tool.id}
                  tool={tool}
                  onRetry={() => regenerate.mutate()}
                />
              ))}
              {pendingApproval && pendingTool && (
                <ApprovalCard
                  approval={pendingApproval}
                  tool={pendingTool}
                  pending={review.isPending}
                  onDecision={(decision, argumentsValue, reason) =>
                    review.mutate({
                      approvalId: pendingApproval.id,
                      decision,
                      argumentsValue,
                      reason,
                    })
                  }
                />
              )}
              {error && <div className="error-banner">{error.message}</div>}
            </section>
            <footer className="composer-shell">
              {activeRun?.run.status === "completed" && (
                <div className="run-actions">
                  <span>Was this result useful?</span>
                  <button onClick={() => feedback.mutate("up")}>👍 Yes</button>
                  <button onClick={() => feedback.mutate("down")}>👎 No</button>
                  <button onClick={() => regenerate.mutate()}>
                    Regenerate
                  </button>
                </div>
              )}
              {activeRun?.run.status === "failed" && (
                <div className="run-actions">
                  <span>The run failed safely.</span>
                  <button onClick={() => regenerate.mutate()}>Retry run</button>
                </div>
              )}
              {activeRun &&
                ["running", "waiting_approval"].includes(
                  activeRun.run.status,
                ) && (
                  <button
                    className="danger stop-run"
                    onClick={() => cancel.mutate()}
                  >
                    Stop run
                  </button>
                )}
              <form
                className="chat-composer"
                onSubmit={(event) => {
                  event.preventDefault();
                  const content = prompt.trim();
                  if (!content) return;
                  setPendingPrompt(content);
                  setEvents([]);
                  run.mutate(content);
                  setPrompt("");
                }}
              >
                <label className="sr-only" htmlFor="copilot-prompt">
                  Message Omni Copilot
                </label>
                <textarea
                  id="copilot-prompt"
                  name="prompt"
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  rows={2}
                  placeholder="Ask about a customer, policy, schedule, or propose an action…"
                  disabled={
                    run.isPending ||
                    activeRun?.run.status === "waiting_approval"
                  }
                  required
                />
                <button
                  type="button"
                  className={
                    recording ? "recording microphone" : "secondary microphone"
                  }
                  disabled={transcribing || run.isPending}
                  onClick={
                    recording ? stopRecording : () => void startRecording()
                  }
                  aria-label={
                    recording ? "Stop recording" : "Record a voice message"
                  }
                >
                  {recording ? "■ Stop" : transcribing ? "…" : "🎙 Speak"}
                </button>
                <button
                  disabled={run.isPending || transcribing || !prompt.trim()}
                >
                  Send
                </button>
              </form>
              {recording && (
                <small className="audio-status" role="status">
                  Listening… click Stop when you finish speaking.
                </small>
              )}
              {transcribing && (
                <small className="audio-status" role="status">
                  Transcribing your recording…
                </small>
              )}
              {audioError && (
                <div className="error-banner audio-error" role="alert">
                  {audioError}
                </div>
              )}
              <small>
                AI can make mistakes. Consequential actions always require your
                approval.
              </small>
            </footer>
          </>
        )}
      </section>
    </main>
  );
}

function FullPageState({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: () => void;
}) {
  return (
    <main className="state-page">
      <h1>{title}</h1>
      {detail && <p>{detail}</p>}
      {action && <button onClick={action}>Sign in again</button>}
    </main>
  );
}
