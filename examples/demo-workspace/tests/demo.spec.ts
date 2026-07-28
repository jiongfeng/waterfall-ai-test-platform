import { expect, test } from "@playwright/test";

test("renders the credential-free demo fixture", async ({ page }) => {
  await page.setContent(`
    <main>
      <h1>Playwright Test Platform</h1>
      <p>This fixture does not contact an external system.</p>
    </main>
  `);

  await expect(
    page.getByRole("heading", { name: "Playwright Test Platform" }),
  ).toBeVisible();
});
