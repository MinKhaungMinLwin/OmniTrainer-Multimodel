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
    "X-Correlation-ID": `voice-e2e-${Date.now()}`,
  };
}

test("reviews a consented voice intake before creating a callback", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const headers = await apiSession(request);
  const configResponse = await request.get(
    `${apiBaseUrl}/api/v1/voice/config`,
    {
      headers,
    },
  );
  const config = await configResponse.json();
  const enabled = await request.put(`${apiBaseUrl}/api/v1/voice/config`, {
    headers,
    data: { ...config, enabled: true },
  });
  expect(enabled.ok()).toBeTruthy();

  await page.goto("/");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Voice Pilot" }).click();
  await expect(
    page.getByRole("heading", { name: "Voice pilot" }),
  ).toBeVisible();

  await page.getByLabel("Name").fill(`E2E Voice Caller ${suffix}`);
  await page.getByLabel("Reason").fill(`E2E callback reason ${suffix}`);
  await page.getByRole("button", { name: "Simulate call" }).click();
  await expect(page.getByText("Callback needs review")).toBeVisible();
  await expect(page.locator(".voice-transcript")).toContainText(
    `E2E Voice Caller ${suffix}`,
  );

  await page.getByLabel("Job title").fill(`E2E Voice Job ${suffix}`);
  await page.getByRole("button", { name: "Approve & create draft" }).click();
  await expect(page.getByText("Callback needs review")).not.toBeVisible();
  await expect(page.locator(".call-inspector")).toContainText(
    "callback_job_created",
  );

  const jobsResponse = await request.get(`${apiBaseUrl}/api/v1/jobs`, {
    headers,
  });
  const jobs = await jobsResponse.json();
  expect(
    jobs.items.some(
      (job: { title: string }) => job.title === `E2E Voice Job ${suffix}`,
    ),
  ).toBeTruthy();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});
