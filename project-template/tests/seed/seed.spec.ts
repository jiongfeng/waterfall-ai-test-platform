import { test } from "@playwright/test";

// waterfall-seed-mode: visit_only
test.describe("seed", () => {
  test("entry", async ({ page }) => {
    await page.goto(process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:8080");
  });
});
