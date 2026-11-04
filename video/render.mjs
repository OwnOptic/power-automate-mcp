// Render the explainer videos into ../docs/video/.
//   node render.mjs              both languages
//   node render.mjs fr           one language
//   node render.mjs --stills     control frames per scene into ./stills, no video
// Run `python make_voice.py` first after any change to narration.json.
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const cli = path.join(here, "node_modules", "@remotion", "cli", "remotion-cli.js");
const timeline = JSON.parse(fs.readFileSync(path.join(here, "src", "timeline.json"), "utf8"));
const args = process.argv.slice(2);
const langs = args.filter((a) => a === "en" || a === "fr");
const run = (a) => execFileSync(process.execPath, [cli, ...a], { stdio: "inherit", cwd: here });

for (const lang of langs.length ? langs : ["en", "fr"]) {
  if (args.includes("--stills")) {
    const dir = path.join(here, "stills", lang);
    fs.mkdirSync(dir, { recursive: true });
    let from = 0;
    for (const s of timeline[lang]) {
      const frame = from + Math.round(s.frames * 0.9); // late in the scene: everything has appeared
      run(["still", "src/index.ts", `Explainer-${lang}`, path.join(dir, `${s.id}.png`), `--frame=${frame}`, "--scale=0.5"]);
      from += s.frames;
    }
  } else {
    const out = path.join(here, "..", "docs", "video", `power-platform-mcp-${lang}.mp4`);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    run(["render", "src/index.ts", `Explainer-${lang}`, out, "--codec=h264", "--crf=26", "--audio-bitrate=96k"]);
    console.log(out);
  }
}
