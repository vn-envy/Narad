import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from 'remotion';

export type TitleCardProps = {
  title: string;
  subtitle: string;
  bg: string;
  accent: string;
};

export const TitleCard: React.FC<TitleCardProps> = ({ title, subtitle, bg, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const scale = spring({ frame, fps, config: { damping: 200 } });
  const subOpacity = interpolate(frame, [15, 35], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: 'Helvetica, Arial, sans-serif',
      }}
    >
      <div style={{ transform: `scale(${scale})`, textAlign: 'center', padding: 80 }}>
        <h1 style={{ color: 'white', fontSize: 110, fontWeight: 800, margin: 0, letterSpacing: -2 }}>
          {title}
        </h1>
        <p style={{ color: accent, fontSize: 44, marginTop: 24, opacity: subOpacity }}>{subtitle}</p>
      </div>
    </AbsoluteFill>
  );
};
