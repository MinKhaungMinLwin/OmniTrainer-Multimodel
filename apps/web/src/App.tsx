import { zodResolver } from "@hookform/resolvers/zod";
import {
  CustomerCreate,
  DraftInvoice,
  Job,
  JobCreate,
  OmniApiClient,
  Tenant,
} from "@omni/contracts";
import {
  QueryClient,
  QueryClientProvider,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8001";
const TOKEN_KEY = "omni.access-token";
const TENANT_KEY = "omni.tenant-id";
const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1 } },
});

const customerSchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200),
  email: z.union([z.literal(""), z.email()]).optional(),
  phone: z.string().max(50).optional(),
  notes: z.string().max(5000).optional(),
});

type CustomerForm = z.infer<typeof customerSchema>;
type Section = "customers" | "jobs" | "schedule" | "invoices";

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
  const tenants = useQuery({
    queryKey: ["tenants"],
    queryFn: () => api.tenants(),
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
    refetchInterval: 30_000,
  });
  const activeTenant = tenantId ?? tenants.data?.[0]?.id ?? null;

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
          <span>
            Assistant <em>Soon</em>
          </span>
        </nav>
      </aside>
      <div className="workspace">
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
        {activeTenant && section === "customers" && (
          <CustomersPage api={api} tenantId={activeTenant} />
        )}
        {activeTenant && section === "jobs" && (
          <JobsPage api={api} tenantId={activeTenant} />
        )}
        {activeTenant && section === "schedule" && (
          <SchedulePage api={api} tenantId={activeTenant} />
        )}
        {activeTenant && section === "invoices" && (
          <InvoicesPage api={api} tenantId={activeTenant} />
        )}
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
  const customers = useQuery({
    queryKey: ["customers", tenantId],
    queryFn: () => api.customers(),
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
              <span>View →</span>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}

function JobsPage({ api, tenantId }: { api: OmniApiClient; tenantId: string }) {
  const cache = useQueryClient();
  const [customerId, setCustomerId] = useState("");
  const [title, setTitle] = useState("");
  const [scheduling, setScheduling] = useState<Job | null>(null);
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
  const refresh = () =>
    cache.invalidateQueries({ queryKey: ["jobs", tenantId] });
  const create = useMutation({
    mutationFn: () =>
      api.createJob({ customer_id: selectedCustomerId, title } as JobCreate),
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
        expected_version: scheduling!.version,
      }),
    onSuccess: async () => {
      setScheduling(null);
      setStartsAt("");
      setEndsAt("");
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
    </main>
  );
}

function SchedulePage({
  api,
  tenantId,
}: {
  api: OmniApiClient;
  tenantId: string;
}) {
  const appointments = useQuery({
    queryKey: ["appointments", tenantId],
    queryFn: () => api.appointments(),
  });
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
      </div>
      {!appointments.data?.length && (
        <div className="empty">
          <h2>No appointments</h2>
          <p>Schedule a draft job to place it here.</p>
        </div>
      )}
      <div className="records">
        {appointments.data?.map((appointment) => (
          <article key={appointment.id}>
            <div className="calendar-date">
              <strong>{new Date(appointment.starts_at).getDate()}</strong>
              <span>
                {new Date(appointment.starts_at).toLocaleString("default", {
                  month: "short",
                })}
              </span>
            </div>
            <div>
              <h2>{new Date(appointment.starts_at).toLocaleString()}</h2>
              <p>
                {appointment.assignee ?? "Unassigned"} · {appointment.timezone}
              </p>
            </div>
            <span>{appointment.status}</span>
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
  const error = draft.error ?? issue.error ?? voidInvoice.error;
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
                · {invoice.status}
              </p>
            </div>
            <div className="row-actions">
              {invoice.status === "draft" && (
                <button onClick={() => issue.mutate(invoice)}>Issue</button>
              )}
              {invoice.status === "issued" && (
                <button
                  className="danger"
                  onClick={() => voidInvoice.mutate(invoice)}
                >
                  Void
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
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
