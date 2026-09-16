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
    "X-Correlation-ID": `automation-e2e-${Date.now()}`,
  };
}

test("rolls a versioned automation from test to reviewed production", async ({
  page,
  request,
}) => {
  const suffix = Date.now();
  const name = `E2E Automation ${suffix}`;
  const headers = await apiSession(request);
  const created = await request.post(`${apiBaseUrl}/api/v1/automations`, {
    headers,
    data: {
      name,
      description: "E2E safe rollout",
      mode: "shadow",
      trigger_type: "call.missed",
      conditions: [{ field: "caller", operator: "exists" }],
      steps: [
        {
          kind: "action",
          operation: "send_message",
          config: {
            channel: "sms",
            recipient: "{{payload.caller}}",
            body: "We missed your call.",
          },
        },
      ],
      approval_rule: { mode: "external" },
      rate_limit_per_hour: 10,
      max_attempts: 3,
    },
  });
  expect(created.ok()).toBeTruthy();
  const definition = await created.json();

  await page.goto("/");
  await page.getByRole("button", { name: "Continue" }).click();
  await page.getByRole("button", { name: "Automations" }).click();
  const card = page
    .locator(".automation-card.installed")
    .filter({ hasText: name });
  await expect(card).toContainText("draft");
  await card.getByRole("button", { name: "Edit & test" }).click();
  await page
    .getByLabel("Test event payload (JSON)")
    .fill(JSON.stringify({ caller: "+15550199" }));
  await page.getByRole("button", { name: "Run without side effects" }).click();
  await expect(
    page
      .locator(".run-card")
      .filter({ hasText: name })
      .filter({ hasText: "shadowed" })
      .first(),
  ).toContainText("Shadow mode evaluated successfully");

  await card.getByRole("button", { name: "Run in shadow" }).click();
  await expect(card).toContainText("active");
  await card.getByRole("button", { name: "Promote" }).click();
  await expect(card).toContainText("production");

  const trigger = await request.post(
    `${apiBaseUrl}/api/v1/automations/triggers`,
    {
      headers,
      data: {
        trigger_type: "call.missed",
        source: "call",
        idempotency_key: `automation-e2e-trigger-${suffix}`,
        payload: { caller: "+15550199" },
      },
    },
  );
  expect(trigger.ok()).toBeTruthy();
  const triggerRuns = await trigger.json();
  const run = triggerRuns.find(
    (item: { definition_id: string }) => item.definition_id === definition.id,
  );
  expect(run.status).toBe("waiting_approval");

  await page.reload();
  await page.getByRole("button", { name: "Automations" }).click();
  const runCard = page
    .locator(".run-card")
    .filter({ hasText: name })
    .filter({ hasText: "waiting_approval" });
  await expect(runCard).toContainText("waiting_approval");
  await runCard.getByRole("button", { name: "Approve" }).click();
  const succeededRun = page
    .locator(".run-card")
    .filter({ hasText: name })
    .filter({ hasText: "succeeded" });
  await expect(succeededRun).toContainText("1 record(s) changed");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});
