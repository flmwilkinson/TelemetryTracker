// Records the guided walkthrough in index.html to 1080p MP4 files.
//
//   npm install            (installs Playwright)
//   npm run record         (needs ffmpeg on PATH)
//
// The page runs in slow motion (?speed=CAPTURE_SPEED) while frames are captured,
// then each frame is re-timed to real speed, so the video stays smooth even though
// a full-HD screenshot takes ~100 ms.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const OUT = path.join(ROOT, 'media');
const CAPTURE_SPEED = 0.15;   // page runs ~6.7x slower than real time while capturing
const DEMO_SECONDS = 15;      // length of the walkthrough
const CUTS = [                // [file name, playback speed]
  ['telemetry-tracker-demo-15s.mp4', 1],
  ['telemetry-tracker-demo-30s.mp4', 0.5],
];

(async () => {
  const frames = fs.mkdtempSync(path.join(os.tmpdir(), 'tt-frames-'));
  fs.mkdirSync(OUT, { recursive: true });

  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + path.join(ROOT, 'index.html') + `?record&speed=${CAPTURE_SPEED}`);
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(500);

  const shots = [];
  const t0 = Date.now();
  await page.evaluate(() => window.startDemo());
  for (let i = 0; ; i++) {
    const t = (Date.now() - t0) / 1000 * CAPTURE_SPEED;   // position in the real-speed timeline
    if (t > DEMO_SECONDS) break;
    const file = path.join(frames, `f${String(i).padStart(5, '0')}.jpg`);
    await page.screenshot({ path: file, type: 'jpeg', quality: 95 });
    shots.push({ file, t });
  }
  await page.screenshot({ path: path.join(OUT, 'poster.png') });
  await browser.close();
  console.log(`captured ${shots.length} frames (${(shots.length / DEMO_SECONDS).toFixed(1)} per demo-second)`);

  for (const [name, playback] of CUTS) {
    const list = path.join(frames, `list-${playback}.txt`);
    const lines = [];
    shots.forEach((s, i) => {
      const next = i + 1 < shots.length ? shots[i + 1].t : DEMO_SECONDS;
      lines.push(`file '${s.file}'`, `duration ${((next - s.t) / playback).toFixed(4)}`);
    });
    lines.push(`file '${shots[shots.length - 1].file}'`);
    fs.writeFileSync(list, lines.join('\n'));
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', list,
      '-vf', 'fps=30,format=yuv420p', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
      '-movflags', '+faststart', path.join(OUT, name)], { stdio: 'inherit' });
    console.log('wrote media/' + name);
  }
  fs.rmSync(frames, { recursive: true, force: true });
})();
