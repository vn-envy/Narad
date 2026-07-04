import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export type LowerThirdProps = {
  name: string;
  role: string;
  accent: string;
};

// Transparent background so this can be composited over other footage.
export const LowerThird: React.FC<LowerThirdProps> = ({ name, role, accent }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({ frame, fps, config: { damping: 200 } });
  const exit = spring({ frame: frame - (durationInFrames - 20), fps, config: { damping: 200 } });
  const x = interpolate(enter, [0, 1], [-600, 0]) + interpolate(exit, [0, 1], [0, -600]);

  return (
    <AbsoluteFill style={{ fontFamily: 'Helvetica, Arial, sans-serif' }}>
      <div style={{ position: 'absolute', left: 120, bottom: 140, transform: `translateX(${x}px)` }}>
        <div style={{ background: 'rgba(11,16,32,0.92)', padding: '22px 40px', borderLeft: `8px solid ${accent}` }}>
          <div style={{ color: 'white', fontSize: 52, fontWeight: 800 }}>{name}</div>
          <div style={{ color: accent, fontSize: 32, marginTop: 6 }}>{role}</div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
