import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const apiBaseUrl = "http://localhost:8001";

async function apiSession(
  request: import("@playwright/test").APIRequestContext,
) {
  const tokenResponse = await request.post(`${apiBaseUrl}/api/v1/dev/token`, {
    data: { email: "owner@omni.example" },
  });
  const { access_token: token } = await tokenResponse.json();
  const tenantsResponse = await request.get(`${apiBaseUrl}/api/v1/tenants`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const [tenant] = await tenantsResponse.json();
  return {
    Authorization: `Bearer ${token}`,
    "X-Tenant-ID": tenant.id as string,
    "X-Correlation-ID": `copilot-e2e-${Date.now()}`,
  };
}

async function signIn(page: import("@playwright/test").Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Customers" })).toBeVisible();
}

test("streams cited answers and reviews an edited write proposal", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const headers = await apiSession(request);
  const customerResponse = await request.post(
    `${apiBaseUrl}/api/v1/customers`,
    {
      headers,
      data: { name: `Copilot Customer ${suffix}` },
    },
  );
  expect(customerResponse.ok()).toBeTruthy();
  const customer = await customerResponse.json();

  await signIn(page);
  await page.getByRole("button", { name: "Assistant" }).click();
  await page.getByRole("button", { name: /new conversation/i }).click();
  await page.getByRole("button", { name: "Manage knowledge" }).click();
  await page.getByLabel("Document title").fill(`E2E cancellation ${suffix}`);
  await page
    .getByLabel("Policy or knowledge text")
    .fill(
      `Cancellation code ${suffix} allows notice at least 24 hours before arrival.`,
    );
  await page.getByRole("button", { name: "Index knowledge" }).click();
  await expect(page.getByText(`E2E cancellation ${suffix}`)).toBeVisible();
  await page.getByRole("button", { name: "Close knowledge" }).click();

  const prompt = page.getByLabel("Message Omni Copilot");
  await prompt.fill(`What is cancellation code ${suffix}?`);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".tool-card").last()).toContainText(
    "search knowledge",
  );
  await expect(page.locator(".tool-card").last()).toContainText("completed");
  await expect(
    page.getByText("I found relevant authorized knowledge.").last(),
  ).toBeVisible();

  await prompt.fill(
    `Create job "Initial AI title" for customer ${customer.id}`,
  );
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByLabel("Human review required")).toBeVisible();
  await page.getByLabel("Approval title").fill(`Reviewed AI job ${suffix}`);
  await page
    .getByPlaceholder("Optional audit note")
    .fill("Dispatcher verified scope");
  await page.getByRole("button", { name: "Save edits & approve" }).click();
  await expect(
    page.getByText(`Created draft job “Reviewed AI job ${suffix}”.`).last(),
  ).toBeVisible();
  await expect(page.locator(".tool-card").last()).toContainText("completed");
  const layout = await page.evaluate(() => {
    const thread = document.querySelector<HTMLElement>(".message-thread");
    const shell = document.querySelector<HTMLElement>(".app-shell");
    return {
      documentHeight: document.documentElement.scrollHeight,
      viewportHeight: window.innerHeight,
      shellHeight: shell?.getBoundingClientRect().height ?? 0,
      threadOverflow: thread ? getComputedStyle(thread).overflowY : "",
    };
  });
  expect(layout.documentHeight).toBeLessThanOrEqual(layout.viewportHeight + 1);
  expect(layout.shellHeight).toBeGreaterThanOrEqual(layout.viewportHeight - 1);
  expect(layout.threadOverflow).toBe("auto");
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
  await page.getByRole("button", { name: "👍 Yes" }).click();

  const jobsResponse = await request.get(`${apiBaseUrl}/api/v1/jobs`, {
    headers,
  });
  const jobs = await jobsResponse.json();
  expect(
    jobs.items.some(
      (job: { title: string }) => job.title === `Reviewed AI job ${suffix}`,
    ),
  ).toBeTruthy();
});
