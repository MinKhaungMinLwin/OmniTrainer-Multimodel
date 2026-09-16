import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { Buffer } from "node:buffer";

const apiBaseUrl = "http://localhost:8001";

async function apiSession(
  request: import("@playwright/test").APIRequestContext,
) {
  const tokenResponse = await request.post(`${apiBaseUrl}/api/v1/dev/token`, {
    data: { email: "owner@omni.example" },
  });
  expect(tokenResponse.ok()).toBeTruthy();
  const { access_token: token } = await tokenResponse.json();
  const tenantsResponse = await request.get(`${apiBaseUrl}/api/v1/tenants`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(tenantsResponse.ok()).toBeTruthy();
  const [tenant] = await tenantsResponse.json();
  return {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Tenant-ID": tenant.id,
      "X-Correlation-ID": `e2e-${Date.now()}`,
    },
  };
}

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
}

test("completes the customer to issued invoice workflow", async ({ page }) => {
  await signIn(page);
  const suffix = Date.now();
  await page.getByRole("button", { name: /new customer/i }).click();
  await page.getByPlaceholder("Acme Services").fill(`E2E Customer ${suffix}`);
  await page
    .getByPlaceholder("ops@example.com")
    .fill(`e2e-${suffix}@example.com`);
  await page.getByRole("button", { name: "Save customer" }).click();
  await expect(page.getByText(`E2E Customer ${suffix}`)).toBeVisible();

  await page.getByRole("button", { name: "Jobs", exact: true }).click();
  await page
    .getByLabel("Customer")
    .selectOption({ label: `E2E Customer ${suffix}` });
  await page.getByLabel("Job title").fill(`E2E Job ${suffix}`);
  await page.getByRole("button", { name: "Create job" }).click();
  const job = page
    .getByRole("article")
    .filter({ hasText: `E2E Job ${suffix}` });
  await expect(job).toContainText("draft");
  await job.getByRole("button", { name: "Schedule" }).click();
  const start = new Date(Date.now() + 48 * 60 * 60 * 1000);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  const local = (date: Date) =>
    new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
      .toISOString()
      .slice(0, 16);
  await page.getByLabel("Starts at").fill(local(start));
  await page.getByLabel("Ends at").fill(local(end));
  await page.getByRole("button", { name: "Confirm schedule" }).click();
  await expect(job).toContainText("scheduled");
  await job.getByRole("button", { name: "Complete" }).click();
  await expect(job).toContainText("completed");

  await page.getByRole("button", { name: "Invoices", exact: true }).click();
  await page
    .getByLabel("Completed job")
    .selectOption({ label: `E2E Job ${suffix}` });
  await page.getByRole("button", { name: "Draft invoice" }).click();
  const invoice = page.getByRole("article").filter({ hasText: "INV-" }).first();
  await expect(invoice).toContainText("draft");
  await invoice.getByRole("button", { name: "Issue" }).click();
  await expect(invoice).toContainText("issued");
});

test("has no serious accessibility violations and exposes offline mode", async ({
  page,
  context,
}) => {
  await page.goto("/");
  const signInResults = await new AxeBuilder({ page }).analyze();
  expect(
    signInResults.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
  await signIn(page);
  const appResults = await new AxeBuilder({ page }).analyze();
  expect(
    appResults.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
  await context.setOffline(true);
  await expect(page.getByRole("status")).toContainText("Offline");
  await expect(
    page.getByRole("button", { name: /new customer/i }),
  ).toBeDisabled();
});

test("round-trips attachments and produces paid invoice artifacts", async ({
  request,
}) => {
  const { headers } = await apiSession(request);
  const suffix = Date.now();
  const commandHeaders = (name: string) => ({
    ...headers,
    "Idempotency-Key": `${name}-${suffix}`,
  });
  const customerResponse = await request.post(
    `${apiBaseUrl}/api/v1/customers`,
    { headers, data: { name: `Artifact Customer ${suffix}` } },
  );
  expect(customerResponse.ok()).toBeTruthy();
  const customer = await customerResponse.json();
  const jobResponse = await request.post(`${apiBaseUrl}/api/v1/jobs`, {
    headers: commandHeaders("job"),
    data: { customer_id: customer.id, title: `Artifact Job ${suffix}` },
  });
  expect(jobResponse.ok()).toBeTruthy();
  const job = await jobResponse.json();
  const uploadResponse = await request.post(
    `${apiBaseUrl}/api/v1/jobs/${job.id}/attachments`,
    {
      headers,
      multipart: {
        file: {
          name: "evidence.txt",
          mimeType: "text/plain",
          buffer: Buffer.from("stored-in-minio"),
        },
      },
    },
  );
  expect(uploadResponse.ok()).toBeTruthy();
  const attachment = await uploadResponse.json();
  const downloadResponse = await request.get(
    `${apiBaseUrl}/api/v1/attachments/${attachment.id}/download`,
    { headers },
  );
  expect(await downloadResponse.text()).toBe("stored-in-minio");
  const scheduleResponse = await request.post(
    `${apiBaseUrl}/api/v1/jobs/${job.id}/schedule`,
    {
      headers: commandHeaders("schedule"),
      data: {
        starts_at: "2030-01-07T09:00:00Z",
        ends_at: "2030-01-07T10:00:00Z",
        timezone: "UTC",
        assignee: `E2E technician ${suffix}`,
        expected_version: 1,
      },
    },
  );
  expect(scheduleResponse.ok()).toBeTruthy();
  const completeResponse = await request.post(
    `${apiBaseUrl}/api/v1/jobs/${job.id}/complete`,
    { headers: commandHeaders("complete"), data: { expected_version: 2 } },
  );
  expect(completeResponse.ok()).toBeTruthy();
  const completed = await completeResponse.json();
  const draftResponse = await request.post(
    `${apiBaseUrl}/api/v1/jobs/${job.id}/invoice`,
    {
      headers: commandHeaders("invoice"),
      data: {
        expected_job_version: completed.version,
        currency: "USD",
        lines: [
          { description: "Service", quantity: 1, unit_price_cents: 12500 },
        ],
      },
    },
  );
  expect(draftResponse.ok()).toBeTruthy();
  const draft = await draftResponse.json();
  const issueResponse = await request.post(
    `${apiBaseUrl}/api/v1/invoices/${draft.id}/issue`,
    {
      headers: commandHeaders("issue"),
      data: { expected_version: draft.version },
    },
  );
  expect(issueResponse.ok()).toBeTruthy();
  const paymentResponse = await request.post(
    `${apiBaseUrl}/api/v1/invoices/${draft.id}/payments`,
    {
      headers: commandHeaders("payment"),
      data: {
        amount_cents: 12500,
        method: "card",
        external_ref: `e2e-${suffix}`,
      },
    },
  );
  expect(paymentResponse.ok()).toBeTruthy();
  const invoicesResponse = await request.get(`${apiBaseUrl}/api/v1/invoices`, {
    headers,
  });
  const invoices = await invoicesResponse.json();
  const paidInvoice = invoices.items.find(
    (invoice: { id: string }) => invoice.id === draft.id,
  );
  expect(paidInvoice).toMatchObject({
    paid_cents: 12500,
    payment_status: "paid",
  });
  const pdfResponse = await request.get(
    `${apiBaseUrl}/api/v1/invoices/${draft.id}/pdf`,
    { headers },
  );
  expect(pdfResponse.headers()["content-type"]).toContain("application/pdf");
  expect((await pdfResponse.body()).subarray(0, 4).toString()).toBe("%PDF");
});
