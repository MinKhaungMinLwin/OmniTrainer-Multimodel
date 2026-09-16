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
    "X-Correlation-ID": `intelligence-e2e-${Date.now()}`,
  };
}

test("models, searches, and traces a completed call", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const marker = `refx${String(suffix).slice(-6)}z`;
  const headers = await apiSession(request);
  const configResponse = await request.get(
    `${apiBaseUrl}/api/v1/voice/config`,
    {
      headers,
    },
  );
  const config = await configResponse.json();
  await request.put(`${apiBaseUrl}/api/v1/voice/config`, {
    headers,
    data: { ...config, enabled: true },
  });
  const simulation = await request.post(
    `${apiBaseUrl}/api/v1/voice/simulations`,
    {
      headers,
      data: {
        caller: `+1555${String(suffix).slice(-7)}`,
        callee: "+15550999",
        region: "local",
        turns: [
          { text: "yes" },
          { text: `E2E Intelligence Caller ${suffix}` },
          {
            text: `The kitchen pipe is leaking, reference ${marker}. Email private@example.com.`,
          },
          { text: "yes" },
        ],
      },
    },
  );
  expect(simulation.ok()).toBeTruthy();
  const call = await simulation.json();
  await expect
    .poll(
      async () =>
        (
          await request.get(
            `${apiBaseUrl}/api/v1/intelligence/calls/${call.id}`,
            { headers },
          )
        ).status(),
      { timeout: 15_000 },
    )
    .toBe(200);

  await page.goto("/");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Intelligence" }).click();
  await expect(
    page.getByRole("heading", { name: "Call intelligence" }),
  ).toBeVisible();
  await page.getByLabel("Search call intelligence").fill(marker);
  const result = page
    .locator(".intelligence-result")
    .filter({ hasText: marker });
  await expect(result).toContainText("plumbing");
  await result.click();
  await expect(page.locator(".intelligence-inspector")).toContainText(
    "Provider records reconciled",
  );
  await expect(page.locator(".intelligence-inspector")).toContainText(
    "[EMAIL]",
  );
  await expect(page.locator(".intelligence-inspector")).not.toContainText(
    "private@example.com",
  );

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});
