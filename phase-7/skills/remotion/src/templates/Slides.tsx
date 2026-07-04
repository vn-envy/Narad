import {
  AbsoluteFill,
  interpolate,
  Sequence,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

export type Slide = { title: string; bullets: string[] };

export type SlidesProps = {
  slides: Slide[];
  secondsPerSlide: number;
  bg: string;
  accent: string;
};

const SlideView: React.FC<{ slide: Slide; accent: string }> = ({ slide, accent }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });
  const x = interpolate(enter, [0, 1], [60, 0]);

  return (
    <AbsoluteFill
      style={{
        justifyContent: 'center',
        padding: '0 160px',
        fontFamily: 'Helvetica, Arial, sans-serif',
        transform: `translateX(${x}px)`,
        opacity: enter,
      }}
    >
      <h1 style={{ color: 'white', fontSize: 84, fontWeight: 800, margin: 0 }}>{slide.title}</h1>
      <ul style={{ marginTop: 40, padding: 0, listStyle: 'none' }}>
        {(slide.bullets ?? []).map((b, i) => {
          const bulletOpacity = interpolate(frame, [10 + i * 8, 22 + i * 8], [0, 1], {
            extrapolateLeft: 'clamp',
            extrapolateRight: 'clamp',
          });
          return (
            <li
              key={i}
              style={{ color: '#d6dae0', fontSize: 44, margin: '18px 0', opacity: bulletOpacity }}
            >
              <span style={{ color: accent, marginRight: 18 }}>▸</span>
              {b}
            </li>
          );
        })}
      </ul>
    </AbsoluteFill>
  );
};

export const Slides: React.FC<SlidesProps> = ({ slides, secondsPerSlide, bg, accent }) => {
  const { fps } = useVideoConfig();
  const per = Math.round((secondsPerSlide ?? 3) * fps);

  return (
    <AbsoluteFill style={{ backgroundColor: bg }}>
      {(slides ?? []).map((slide, i) => (
        <Sequence key={i} from={i * per} durationInFrames={per}>
          <SlideView slide={slide} accent={accent} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
