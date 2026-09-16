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

  customers(): Promise<CustomerList> {
    return this.request("/api/v1/customers", {}, true, true);
  }

  createCustomer(customer: CustomerCreate): Promise<Customer> {
    return this.request(
      "/api/v1/customers",
      { method: "POST", body: JSON.stringify(customer) },
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

  invoices(): Promise<InvoiceList> {
    return this.request("/api/v1/invoices", {}, true, true);
  }

  draftInvoice(jobId: string, invoice: DraftInvoice): Promise<Invoice> {
    return this.command(`/api/v1/jobs/${jobId}/invoice`, invoice);
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
    const headers = new Headers(init.headers);
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
    return (await response.json()) as T;
  }
}
