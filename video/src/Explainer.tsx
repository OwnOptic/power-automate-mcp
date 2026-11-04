import React from "react";
import {
  AbsoluteFill,
  Audio,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import timeline from "./timeline.json";

export type Lang = "en" | "fr";

// The repo's own palette, the one docs/*.svg use.
const NAVY = "#2A3B4E";
const ORANGE = "#F26F21";
const MUTED = "#5B6B7C";
const BG = "#F4F6F9";
const RED = "#B42318";
const GREEN = "#1F7A4D";
const SANS = "Segoe UI, -apple-system, Helvetica, Arial, sans-serif";
const MONO = "Cascadia Mono, Consolas, Menlo, monospace";

type Caption = { start: number; end: number; text: string };
type Scene = { id: string; frames: number; lead: number; captions: Caption[] };

// On-screen labels. The voice carries the explanation; these only name things.
const T = {
  en: {
    subtitle: "Claude builds, runs and debugs your Power Automate flows, then gives them to your Copilot Studio agents.",
    local: "runs locally, as you: your az login",
    clicks: "clicks to find why it failed",
    record: "What the API returns for a failed connector action",
    noError: "no error property",
    blob: "the real error is behind this link",
    loop: "The loop, as it ran live",
    agentTitle: "Give the agent a tool",
    rule: "Agents only call solution flows",
    ruleBad: "create_flow: My flows, no Dataverse row",
    ruleGood: "create_agent_flow: a solution flow",
    skills: "Skills: whole procedures",
    spec: "schema/orders.example.json",
    pipeline: "solution-from-base, with pac",
    steps: ["export", "unpack", "copy a column", "version 1.1.0.0", "pack", "import"],
    verified: "pmd_trackingnumber exists in Dataverse",
    install: "Install in ten minutes",
    config: ".mcp.json: three local servers",
    takeaways: [
      "Auth and transport are commodity.",
      "Shaping and docstrings are what you own.",
      "Agents call solution flows. Describe the tool well.",
    ],
  },
  fr: {
    subtitle: "Claude construit, lance et débogue vos flux Power Automate, puis les confie à vos agents Copilot Studio.",
    local: "tourne en local, en votre nom : votre az login",
    clicks: "clics pour trouver pourquoi il a échoué",
    record: "Ce que l'API renvoie pour une action de connecteur en échec",
    noError: "aucune propriété error",
    blob: "la vraie erreur est derrière ce lien",
    loop: "La boucle, exécutée en direct",
    agentTitle: "Donner un outil à l'agent",
    rule: "Les agents n'appellent que des flux de solution",
    ruleBad: "create_flow : Mes flux, aucune ligne Dataverse",
    ruleGood: "create_agent_flow : un flux de solution",
    skills: "Skills : des procédures entières",
    spec: "schema/orders.example.json",
    pipeline: "solution-from-base, avec pac",
    steps: ["export", "unpack", "copier une colonne", "version 1.1.0.0", "pack", "import"],
    verified: "pmd_trackingnumber existe dans Dataverse",
    install: "Installé en dix minutes",
    config: ".mcp.json : trois serveurs locaux",
    takeaways: [
      "L'authentification et le transport sont banals.",
      "La mise en forme et les docstrings vous appartiennent.",
      "Les agents appellent des flux de solution. Décrivez bien l'outil.",
    ],
  },
} as const;

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

const useEnter = (delay = 0) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping: 200 } });
};

const Card: React.FC<{ children: React.ReactNode; style?: React.CSSProperties; delay?: number }> = ({ children, style, delay = 0 }) => {
  const p = useEnter(delay);
  return (
    <div style={{
      background: "#FFFFFF", borderRadius: 18, boxShadow: "0 12px 40px rgba(42,59,78,0.10)",
      border: "1.5px solid #DDE3EA", opacity: p, transform: `translateY(${(1 - p) * 30}px)`, ...style,
    }}>{children}</div>
  );
};

const Heading: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const p = useEnter(0);
  return (
    <div style={{ position: "absolute", top: 70, left: 110, display: "flex", alignItems: "center", gap: 22, opacity: p }}>
      <div style={{ width: 10, height: 54, background: ORANGE, borderRadius: 5 }} />
      <div style={{ fontFamily: SANS, fontSize: 52, fontWeight: 700, color: NAVY }}>{children}</div>
    </div>
  );
};

type Line = { at: number; text: string; tone?: "cmd" | "ok" | "err" | "out" | "dim" };

// A terminal that types its lines as the scene plays. `at` is a fraction of the scene,
// so the same beats land on the narration in both languages.
const Terminal: React.FC<{ lines: Line[]; frames: number; style?: React.CSSProperties; title?: string }> = ({ lines, frames, style, title }) => {
  const frame = useCurrentFrame();
  const colors = { cmd: "#FFFFFF", ok: "#7FE0A8", err: "#FF8A80", out: "#C9D6E3", dim: "#7F93A8" };
  return (
    <Card style={{ background: "#1B2633", border: "1.5px solid #2E3E50", padding: "26px 34px", ...style }}>
      <div style={{ display: "flex", gap: 10, marginBottom: 18, alignItems: "center" }}>
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => <div key={c} style={{ width: 14, height: 14, borderRadius: 7, background: c }} />)}
        <div style={{ fontFamily: SANS, color: "#7F93A8", fontSize: 20, marginLeft: 14 }}>{title ?? "Claude Code"}</div>
      </div>
      {lines.map((l, i) => {
        const start = l.at * frames;
        const shown = Math.max(0, Math.floor((frame - start) * 2.2));
        if (frame < start) return null;
        const text = l.text.slice(0, shown);
        return (
          <div key={i} style={{ fontFamily: MONO, fontSize: 25, lineHeight: 1.55, color: colors[l.tone ?? "out"], whiteSpace: "pre-wrap" }}>
            {l.tone === "cmd" ? <span style={{ color: ORANGE }}>{"> "}</span> : "  "}{text}
          </div>
        );
      })}
    </Card>
  );
};

const Captions: React.FC<{ captions: Caption[] }> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = frame / fps;
  const current = captions.find((c) => t >= c.start && t < c.end + 0.25);
  if (!current) return null;
  return (
    <div style={{ position: "absolute", bottom: 46, left: 0, right: 0, display: "flex", justifyContent: "center" }}>
      <div style={{
        maxWidth: 1560, background: "rgba(27,38,51,0.88)", color: "#FFFFFF", fontFamily: SANS, fontSize: 31,
        lineHeight: 1.4, padding: "14px 30px", borderRadius: 12, textAlign: "center",
      }}>{current.text}</div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Scenes
// ---------------------------------------------------------------------------

const Title: React.FC<{ lang: Lang }> = ({ lang }) => {
  const p = useEnter(5);
  const chips = ["server.py  10", "extras.py  9", "copilot_studio.py  9", "+ microsoft-learn"];
  return (
    <AbsoluteFill style={{ background: NAVY, justifyContent: "center", padding: "0 160px" }}>
      <div style={{ width: 140, height: 10, background: ORANGE, borderRadius: 5, marginBottom: 40, transform: `scaleX(${p})`, transformOrigin: "left" }} />
      <div style={{ fontFamily: MONO, fontSize: 104, fontWeight: 700, color: "#FFFFFF", opacity: p }}>power-platform-mcp</div>
      <div style={{ fontFamily: SANS, fontSize: 40, color: "#D5DEE8", marginTop: 26, maxWidth: 1400, lineHeight: 1.35, opacity: useEnter(18) }}>{T[lang].subtitle}</div>
      <div style={{ display: "flex", gap: 20, marginTop: 56 }}>
        {chips.map((c, i) => {
          const q = useEnter(36 + i * 8);
          return <div key={c} style={{ fontFamily: MONO, fontSize: 26, color: "#FFFFFF", border: `2px solid ${i < 3 ? ORANGE : "#5B6B7C"}`, padding: "12px 22px", borderRadius: 40, opacity: q, transform: `translateY(${(1 - q) * 20}px)` }}>{c}</div>;
        })}
      </div>
      <div style={{ fontFamily: SANS, fontSize: 28, color: "#9FB0C2", marginTop: 34, opacity: useEnter(70) }}>{T[lang].local}</div>
    </AbsoluteFill>
  );
};

const Problem: React.FC<{ lang: Lang; frames: number }> = ({ lang, frames }) => {
  const frame = useCurrentFrame();
  const steps = lang === "fr"
    ? ["Flux", "Historique", "Exécution", "Action", "Entrées", "Sorties", "Action précédente", "Ses sorties"]
    : ["Flow", "Run history", "The run", "The action", "Inputs", "Outputs", "Earlier action", "Its outputs"];
  const clicks = Math.min(12, Math.floor(interpolate(frame, [20, frames * 0.42], [0, 12], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })));
  const shown = Math.floor(interpolate(frame, [20, frames * 0.42], [0, steps.length], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const json = useEnter(frames * 0.45);
  return (
    <AbsoluteFill style={{ background: BG }}>
      <div style={{ position: "absolute", left: 110, top: 120, width: 640 }}>
        <div style={{ fontFamily: SANS, fontSize: 150, fontWeight: 800, color: ORANGE, lineHeight: 1 }}>{clicks}</div>
        <div style={{ fontFamily: SANS, fontSize: 34, color: NAVY, fontWeight: 600, marginBottom: 30 }}>{T[lang].clicks}</div>
        {steps.slice(0, shown).map((s, i) => (
          <div key={s} style={{ fontFamily: SANS, fontSize: 28, color: MUTED, padding: "8px 0", borderBottom: "1px solid #DDE3EA" }}>
            <span style={{ color: ORANGE, fontWeight: 700, marginRight: 14 }}>{i + 1}</span>{s}
          </div>
        ))}
      </div>
      <Card delay={frames * 0.45} style={{ position: "absolute", right: 110, top: 140, width: 900, padding: "34px 40px", opacity: json }}>
        <div style={{ fontFamily: SANS, fontSize: 26, fontWeight: 700, color: NAVY, marginBottom: 20 }}>{T[lang].record}</div>
        <pre style={{ fontFamily: MONO, fontSize: 25, lineHeight: 1.55, color: NAVY, margin: 0 }}>
{`{
  "name": "Post_message",
  "properties": {
    "status": "Failed",
    "code": "NotFound",`}
<span style={{ background: "#FDECEA", color: RED }}>{`    // ${T[lang].noError}`}</span>{`
    "outputsLink": {
`}<span style={{ background: "#FFF4EC", color: ORANGE }}>{`      "uri": "https://...usercontent.com/...&sig=..."`}</span>{`
    }
  }
}`}
        </pre>
        <div style={{ fontFamily: SANS, fontSize: 24, color: ORANGE, marginTop: 18, fontWeight: 600 }}>{T[lang].blob}</div>
      </Card>
    </AbsoluteFill>
  );
};

const Diagram: React.FC<{ src: string }> = ({ src }) => {
  const p = useEnter(4);
  return (
    <AbsoluteFill style={{ background: "#FFFFFF", justifyContent: "center", alignItems: "center", paddingBottom: 90 }}>
      <Img src={staticFile(src)} style={{ width: 1640, opacity: p, transform: `scale(${0.94 + p * 0.06})` }} />
    </AbsoluteFill>
  );
};

const Loop: React.FC<{ lang: Lang; frames: number }> = ({ lang, frames }) => (
  <AbsoluteFill style={{ background: BG }}>
    <Heading>{T[lang].loop}</Heading>
    <Terminal frames={frames} style={{ position: "absolute", left: 110, right: 110, top: 170, bottom: 190 }} lines={[
      { at: 0.03, tone: "cmd", text: 'create_flow("DEMO - nightly batch", demo-flow.json)' },
      { at: 0.08, tone: "ok", text: "Started   2.3 s" },
      { at: 0.12, tone: "cmd", text: "run_flow  ->  list_runs" },
      { at: 0.16, tone: "err", text: "Failed" },
      { at: 0.22, tone: "cmd", text: "explain_run" },
      { at: 0.27, tone: "err", text: "Compute_batches: Attempt to divide an integral or decimal value by zero in function 'div'." },
      { at: 0.38, tone: "out", text: 'Load_settings emitted {"region": "westeurope", "retries": 3, "batch_size": 0}' },
      { at: 0.55, tone: "cmd", text: "update_flow_definition   batch_size 0 -> 4   (previous definition kept)" },
      { at: 0.63, tone: "cmd", text: "run_flow  ->  list_runs" },
      { at: 0.67, tone: "ok", text: "Succeeded" },
      { at: 0.74, tone: "cmd", text: "compare_runs(failed, succeeded)" },
      { at: 0.79, tone: "out", text: "diverged_at: Compute_batches" },
      { at: 0.84, tone: "ok", text: "output_changes: Load_settings  batch_size  failed 0  /  worked 4" },
    ]} />
  </AbsoluteFill>
);

const Agent: React.FC<{ lang: Lang; frames: number }> = ({ lang, frames }) => {
  const rule = useEnter(frames * 0.2);
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Heading>{T[lang].agentTitle}</Heading>
      <Terminal frames={frames} title="power-platform-copilot" style={{ position: "absolute", left: 110, width: 1080, top: 170, bottom: 190 }} lines={[
        { at: 0.02, tone: "cmd", text: "list_agents  ->  get_agent(Store Helper)" },
        { at: 0.07, tone: "out", text: "instructions: (empty)   tools: none" },
        { at: 0.2, tone: "cmd", text: 'create_agent_flow("DEMO - Order status")' },
        { at: 0.26, tone: "ok", text: "solution flow, Started" },
        { at: 0.36, tone: "cmd", text: 'add_flow_tool("Get order status")' },
        { at: 0.41, tone: "ok", text: "added   inputs: orderId" },
        { at: 0.45, tone: "ok", text: "        outputs: status, eta, carrier" },
        { at: 0.54, tone: "cmd", text: 'update_agent_instructions("Return {json} ...")' },
        { at: 0.59, tone: "err", text: "Refused: braces are read as Power Fx" },
        { at: 0.65, tone: "cmd", text: "update_agent_instructions(...)" },
        { at: 0.69, tone: "ok", text: "updated   previous_instructions kept" },
        { at: 0.78, tone: "cmd", text: "publish_agent" },
        { at: 0.86, tone: "ok", text: "Succeeded   33 s" },
      ]} />
      <Card delay={frames * 0.2} style={{ position: "absolute", right: 110, width: 560, top: 170, padding: "34px 36px", opacity: rule }}>
        <div style={{ fontFamily: SANS, fontSize: 30, fontWeight: 700, color: NAVY, lineHeight: 1.3, marginBottom: 28 }}>{T[lang].rule}</div>
        <div style={{ fontFamily: SANS, fontSize: 25, color: RED, padding: "16px 18px", background: "#FDECEA", borderRadius: 10, marginBottom: 16 }}>✕  {T[lang].ruleBad}</div>
        <div style={{ fontFamily: SANS, fontSize: 25, color: GREEN, padding: "16px 18px", background: "#E8F5EE", borderRadius: 10 }}>✓  {T[lang].ruleGood}</div>
      </Card>
    </AbsoluteFill>
  );
};

const Skills: React.FC<{ lang: Lang; frames: number }> = ({ lang, frames }) => {
  const frame = useCurrentFrame();
  const steps = T[lang].steps;
  const shown = Math.floor(interpolate(frame, [frames * 0.42, frames * 0.82], [0, steps.length], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }));
  const done = useEnter(frames * 0.86);
  return (
    <AbsoluteFill style={{ background: BG }}>
      <Heading>{T[lang].skills}</Heading>
      <Card delay={8} style={{ position: "absolute", left: 110, width: 760, top: 170, padding: "28px 34px" }}>
        <div style={{ fontFamily: MONO, fontSize: 22, color: MUTED, marginBottom: 14 }}>{T[lang].spec}</div>
        <pre style={{ fontFamily: MONO, fontSize: 23, lineHeight: 1.5, color: NAVY, margin: 0 }}>{`"tables": [{
  "name": "Order",
  "columns": [
    {"name": "Status",   "type": "choice"},
    {"name": "Amount",   "type": "decimal"},
    {"name": "Due date", "type": "date"},
    {"name": "Customer", "type": "lookup",
     "target": "account"}
  ]}]`}</pre>
        <div style={{ fontFamily: MONO, fontSize: 22, color: GREEN, marginTop: 18 }}>apply  -&gt;  table pmd_order: created, published</div>
      </Card>
      <div style={{ position: "absolute", left: 930, right: 110, top: 170 }}>
        <div style={{ fontFamily: SANS, fontSize: 30, fontWeight: 700, color: NAVY, marginBottom: 26 }}>{T[lang].pipeline}</div>
        {steps.slice(0, shown).map((s, i) => (
          <div key={s} style={{ display: "flex", alignItems: "center", gap: 18, marginBottom: 16 }}>
            <div style={{ width: 46, height: 46, borderRadius: 23, background: ORANGE, color: "#FFFFFF", fontFamily: SANS, fontWeight: 700, fontSize: 22, display: "flex", alignItems: "center", justifyContent: "center" }}>{i + 1}</div>
            <div style={{ fontFamily: MONO, fontSize: 27, color: NAVY }}>{s}</div>
          </div>
        ))}
        <div style={{ marginTop: 18, fontFamily: SANS, fontSize: 27, color: GREEN, fontWeight: 600, background: "#E8F5EE", padding: "16px 22px", borderRadius: 10, opacity: done }}>✓  {T[lang].verified}</div>
      </div>
    </AbsoluteFill>
  );
};

const Install: React.FC<{ lang: Lang; frames: number }> = ({ lang, frames }) => (
  <AbsoluteFill style={{ background: BG }}>
    <Heading>{T[lang].install}</Heading>
    <Terminal frames={frames} title="PowerShell" style={{ position: "absolute", left: 110, width: 900, top: 170, bottom: 190 }} lines={[
      { at: 0.04, tone: "cmd", text: "git clone https://github.com/OwnOptic/power-platform-mcp" },
      { at: 0.14, tone: "cmd", text: "python -m venv .venv" },
      { at: 0.22, tone: "cmd", text: ".venv\\Scripts\\pip install -r requirements.txt" },
      { at: 0.36, tone: "cmd", text: '$env:AZURE_CONFIG_DIR = "$HOME\\.azure-demo"' },
      { at: 0.44, tone: "cmd", text: "az login --tenant <demo tenant>" },
      { at: 0.56, tone: "ok", text: "ready" },
    ]} />
    <Card delay={frames * 0.6} style={{ position: "absolute", right: 110, width: 780, top: 170, padding: "28px 34px" }}>
      <div style={{ fontFamily: MONO, fontSize: 22, color: MUTED, marginBottom: 14 }}>{T[lang].config}</div>
      <pre style={{ fontFamily: MONO, fontSize: 22, lineHeight: 1.55, color: NAVY, margin: 0 }}>{`"power-platform":
  { "command": "python",
    "args": ["server.py"],
    "env": { "AZURE_CONFIG_DIR": ... } },
"power-platform-extras":  { ... },
"power-platform-copilot": { ... },
"microsoft-learn":
  { "type": "http", "url": ... }`}</pre>
    </Card>
  </AbsoluteFill>
);

const Outro: React.FC<{ lang: Lang }> = ({ lang }) => (
  <AbsoluteFill style={{ background: NAVY, padding: "150px 160px" }}>
    {T[lang].takeaways.map((t, i) => {
      const p = useEnter(10 + i * 40);
      return (
        <div key={t} style={{ display: "flex", alignItems: "center", gap: 28, marginBottom: 40, opacity: p, transform: `translateX(${(1 - p) * -30}px)` }}>
          <div style={{ width: 58, height: 58, borderRadius: 29, background: ORANGE, color: "#FFFFFF", fontFamily: SANS, fontSize: 30, fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>{i + 1}</div>
          <div style={{ fontFamily: SANS, fontSize: 46, color: "#FFFFFF", fontWeight: 600 }}>{t}</div>
        </div>
      );
    })}
    <div style={{ fontFamily: MONO, fontSize: 44, color: ORANGE, marginTop: 50, opacity: useEnter(150) }}>github.com/OwnOptic/power-platform-mcp</div>
  </AbsoluteFill>
);

// ---------------------------------------------------------------------------

export const Explainer: React.FC<{ lang: Lang }> = ({ lang }) => {
  const scenes = timeline[lang] as Scene[];
  let from = 0;
  return (
    <AbsoluteFill style={{ background: BG }}>
      {scenes.map((s) => {
        const start = from;
        from += s.frames;
        const body = {
          title: <Title lang={lang} />,
          problem: <Problem lang={lang} frames={s.frames} />,
          layers: <Diagram src="img/four-layers.svg" />,
          loop: <Loop lang={lang} frames={s.frames} />,
          hop: <Diagram src="img/explain-run.svg" />,
          agent: <Agent lang={lang} frames={s.frames} />,
          skills: <Skills lang={lang} frames={s.frames} />,
          install: <Install lang={lang} frames={s.frames} />,
          outro: <Outro lang={lang} />,
        }[s.id];
        return (
          <Sequence key={s.id} from={start} durationInFrames={s.frames} name={s.id}>
            {body}
            {/* The voice starts after the lead silence the caption timings already include. */}
            <Sequence from={Math.round(s.lead * timeline.fps)}>
              <Audio src={staticFile(`audio/${lang}/${s.id}.mp3`)} />
            </Sequence>
            <Captions captions={s.captions} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
