#!/usr/bin/env node
// Capture the public product-tour animations used by the README.
//
// The marketing site's three device demos are rendered CSS/JS animations, not
// normal video files. This script opens the public page with Playwright, replays
// each interaction, and captures the element at a fixed cadence.
//
// Usage:
//
//     node scripts/capture_production_tour.mjs /tmp/jkinco-tour-frames
//
// The Python builder wraps this script with a temporary directory and compiles
// the frames into optimised GIFs.

import { mkdir, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const requireFromFrontend = createRequire(join(ROOT, "frontend", "package.json"));
const { chromium } = requireFromFrontend("playwright");

const outputRoot = process.argv[2];
if (!outputRoot) {
  throw new Error("Pass an output directory: node scripts/capture_production_tour.mjs <dir>");
}

const intervalMs = 150;
const durationMs = 3_600;
const demos = [
  { kind: "recording", label: "Live capture" },
  { kind: "generation", label: "Automatic minutes" },
  { kind: "sona", label: "Meeting Q&A" },
];

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.CHROME_PATH ||
    (process.platform === "darwin"
      ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      : undefined),
});

try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 2,
    reducedMotion: "no-preference",
    colorScheme: "light",
  });
  const page = await context.newPage();

  await page.goto("https://jkincolisten.cloud/download/ios", {
    waitUntil: "networkidle",
    timeout: 45_000,
  });
  await page.evaluate(() => document.fonts.ready);

  const manifest = {
    source: "https://jkincolisten.cloud/download/ios",
    capturedAt: new Date().toISOString(),
    intervalMs,
    durationMs,
    demos: [],
  };

  for (const demo of demos) {
    const dir = join(outputRoot, demo.kind);
    await mkdir(dir, { recursive: true });

    const locator = page.locator(`[data-demo="${demo.kind}"]`);
    await locator.scrollIntoViewIfNeeded();
    await page.waitForTimeout(350);

    // Dispatch on the element itself so the page's replay handler receives the
    // event regardless of where the animation sheet currently is in the card.
    await locator.dispatchEvent("click");

    const started = Date.now();
    const frameCount = Math.floor(durationMs / intervalMs) + 1;
    for (let index = 0; index < frameCount; index += 1) {
      const target = started + index * intervalMs;
      const delay = target - Date.now();
      if (delay > 0) await page.waitForTimeout(delay);

      const name = `frame-${String(index).padStart(3, "0")}.png`;
      await locator.screenshot({
        path: join(dir, name),
        animations: "allow",
        caret: "hide",
      });
    }

    manifest.demos.push({
      ...demo,
      frameCount,
      firstFrame: join(demo.kind, "frame-000.png"),
      lastFrame: join(demo.kind, `frame-${String(frameCount - 1).padStart(3, "0")}.png`),
    });
  }

  await writeFile(join(outputRoot, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
} finally {
  await browser.close();
}
