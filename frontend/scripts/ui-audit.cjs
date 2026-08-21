const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const baseUrl = process.env.JKINCO_BASE_URL || "http://127.0.0.1:8080";
const username = process.env.JKINCO_TEST_USER;
const password = process.env.JKINCO_TEST_PASSWORD;
const outputDir = path.resolve(process.argv[2] || "artifacts/ui-audit");
const viewports = [
  { name: "desktop", width: 1440, height: 1000 },
  { name: "tablet", width: 768, height: 1024 },
  { name: "mobile", width: 390, height: 844 },
];

async function login(page) {
  await page.goto(baseUrl, { waitUntil: "networkidle", timeout: 60_000 });
  if (await page.locator(".login-card").count()) {
    if (!username || !password) {
      throw new Error("UI audit requires JKINCO_TEST_USER and JKINCO_TEST_PASSWORD");
    }
    await page.getByLabel("用户名").fill(username);
    await page.getByLabel("密码").fill(password);
    await page.locator(".login-submit").click();
  }
  await page.locator(".app-shell").waitFor({ timeout: 30_000 });
}

async function collectMetrics(page) {
  return page.evaluate(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.opacity !== "0" && !element.closest('[aria-hidden="true"], [hidden]');
    };
    const minimumTarget = innerWidth <= 700 ? 44 : 36;
    const interactiveElements = [...document.querySelectorAll("button, a, input, select")]
      .filter((element) => element.getAttribute("type") !== "file")
      .filter(visible);
    const smallTargets = interactiveElements
      .map((element) => {
        const isChoice = element.matches('input[type="checkbox"], input[type="radio"]');
        const target = isChoice ? element.closest("label") || element : element;
        const rect = target.getBoundingClientRect();
        return {
          label: element.getAttribute("aria-label") || element.textContent?.trim().slice(0, 40) || element.tagName,
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
      })
      .filter((item) => item.width < minimumTarget || item.height < minimumTarget);
    const clippedText = [...document.querySelectorAll("button, a, h1, h2, h3, p, span, b, small")]
      .filter(visible)
      .filter((element) => {
        const style = getComputedStyle(element);
        const lineClamp = style.getPropertyValue("-webkit-line-clamp");
        if (["auto", "scroll"].includes(style.overflowX) || style.textOverflow === "ellipsis" || (lineClamp && lineClamp !== "none")) return false;
        return element.scrollWidth > element.clientWidth + 2 || element.scrollHeight > element.clientHeight + 2;
      })
      .slice(0, 20)
      .map((element) => ({
        label: element.textContent?.trim().slice(0, 60) || element.tagName,
        className: element.className?.toString().slice(0, 80) || "",
      }));
    const fixedElements = [...document.querySelectorAll("body *")]
      .filter(visible)
      .filter((element) => ["fixed", "sticky"].includes(getComputedStyle(element).position))
      .slice(0, 30)
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          className: element.className?.toString().slice(0, 80) || element.tagName,
          left: Math.round(rect.left), top: Math.round(rect.top),
          right: Math.round(rect.right), bottom: Math.round(rect.bottom),
        };
      });
    return {
      viewportWidth: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
      smallTargets,
      clippedText,
      fixedElements,
    };
  });
}

async function capture(page, viewport, name, report, fullPage = true) {
  const filename = `${viewport.name}-${name}.png`;
  await page.screenshot({ path: path.join(outputDir, filename), fullPage });
  report[`${viewport.name}:${name}`] = await collectMetrics(page);
}

async function clickSidebarAction(page, viewport, name) {
  if (viewport.width <= 1100) {
    await page.getByRole("button", { name: "打开导航" }).click();
    await page.locator(".sidebar:not(.collapsed)").waitFor();
  }
  const action = page.locator(".sidebar").getByRole("button", { name, exact: true });
  await action.evaluate(element => element.click());
}

async function auditApplication(page, viewport, report) {
  await capture(page, viewport, "workspace", report);

  await clickSidebarAction(page, viewport, "历史会议");
  await page.locator(".history-view").waitFor();
  await capture(page, viewport, "history", report);

  await clickSidebarAction(page, viewport, "录音纪要");
  await page.locator(".workspace").waitFor();
  await page.getByRole("tab", { name: "客户拜访", exact: true }).click();
  await capture(page, viewport, "customer-visit", report);

  await page.getByRole("button", { name: "打开问筑听" }).click();
  await page.locator('.assistant-drawer.open').waitFor();
  await page.waitForTimeout(250);
  await capture(page, viewport, "assistant", report, false);
  await page.getByRole("button", { name: "关闭问筑听" }).click();

  await page.getByRole("button", { name: "账户菜单" }).click();
  await page.getByRole("menuitem", { name: "个人信息" }).click();
  await page.locator(".profile-modal").waitFor();
  await capture(page, viewport, "profile", report, false);
  await page.getByRole("button", { name: "关闭个人信息" }).click();

  await clickSidebarAction(page, viewport, "开始会议");
  await page.locator(".meeting-lobby").waitFor({ timeout: 30_000 });
  await capture(page, viewport, "meeting-lobby", report);

  const lobbyStart = page.locator(".meeting-lobby-header").getByRole("button", { name: "开始会议", exact: true });
  await lobbyStart.click();
  await page.locator(".meeting-dialog").waitFor();
  await capture(page, viewport, "meeting-dialog", report, false);
  await page.getByRole("button", { name: "关闭创建会议" }).click();
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    args: ["--ignore-certificate-errors"],
  });
  const report = {};
  try {
    for (const viewport of viewports) {
      const context = await browser.newContext({ viewport, deviceScaleFactor: 1 });
      const page = await context.newPage();
      await login(page);
      await auditApplication(page, viewport, report);
      await context.close();
    }
  } finally {
    await browser.close();
  }
  const failures = Object.entries(report).flatMap(([screen, metrics]) => {
    const items = [];
    if (metrics.horizontalOverflow) items.push(`${screen}: horizontal overflow (${metrics.scrollWidth}/${metrics.viewportWidth})`);
    if (metrics.smallTargets.length) items.push(`${screen}: undersized controls ${JSON.stringify(metrics.smallTargets)}`);
    if (metrics.clippedText.length) items.push(`${screen}: clipped text ${JSON.stringify(metrics.clippedText)}`);
    return items;
  });
  fs.writeFileSync(path.join(outputDir, "report.json"), `${JSON.stringify(report, null, 2)}\n`);
  const summary = {
    screens: Object.keys(report).length,
    horizontalOverflow: Object.values(report).filter(item => item.horizontalOverflow).length,
    undersizedControls: Object.values(report).reduce((total, item) => total + item.smallTargets.length, 0),
    clippedText: Object.values(report).reduce((total, item) => total + item.clippedText.length, 0),
  };
  process.stdout.write(`UI audit summary: ${JSON.stringify(summary)}\n`);
  if (failures.length) throw new Error(`UI release gate failed:\n${failures.join("\n")}`);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
