import React from "react";
import { Composition } from "remotion";
import { Explainer, Lang } from "./Explainer";
import timeline from "./timeline.json";

const total = (lang: Lang) => timeline[lang].reduce((sum, s) => sum + s.frames, 0);

export const Root: React.FC = () => (
  <>
    {(["en", "fr"] as const).map((lang) => (
      <Composition
        key={lang}
        id={`Explainer-${lang}`}
        component={Explainer}
        durationInFrames={total(lang)}
        fps={timeline.fps}
        width={1920}
        height={1080}
        defaultProps={{ lang }}
      />
    ))}
  </>
);
