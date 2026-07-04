import { Composition } from 'remotion';
import { TitleCard } from './templates/TitleCard';
import { Slides } from './templates/Slides';
import { LowerThird } from './templates/LowerThird';
import { CodeReveal } from './templates/CodeReveal';
import { Custom } from './generated/Custom';

const FPS = 30;
const W = 1920;
const H = 1080;

// Any composition may override fps / dimensions / duration through its own
// props (durationInFrames, fps, width, height, durationSeconds). calculateMetadata
// keeps the template library flexible without a fixed timeline.
const metaFrom = (fallbackFrames: number) => ({ props }: { props: any }) => {
  const fps = props.fps ?? FPS;
  const durationInFrames =
    props.durationInFrames ??
    (props.durationSeconds ? Math.round(props.durationSeconds * fps) : fallbackFrames);
  return {
    fps,
    width: props.width ?? W,
    height: props.height ?? H,
    durationInFrames: Math.max(1, durationInFrames),
  };
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="TitleCard"
        component={TitleCard}
        durationInFrames={120}
        fps={FPS}
        width={W}
        height={H}
        defaultProps={{ title: 'Narad', subtitle: 'Made with Remotion', bg: '#0b1020', accent: '#e94f37' }}
        calculateMetadata={metaFrom(120)}
      />

      <Composition
        id="Slides"
        component={Slides}
        durationInFrames={300}
        fps={FPS}
        width={W}
        height={H}
        defaultProps={{
          slides: [
            { title: 'First slide', bullets: ['Point one', 'Point two'] },
            { title: 'Second slide', bullets: ['Another point'] },
          ],
          secondsPerSlide: 3,
          bg: '#0b1020',
          accent: '#e94f37',
        }}
        calculateMetadata={({ props }: { props: any }) => {
          const fps = props.fps ?? FPS;
          const count = Math.max(1, (props.slides ?? []).length);
          return {
            fps,
            width: props.width ?? W,
            height: props.height ?? H,
            durationInFrames: count * Math.round((props.secondsPerSlide ?? 3) * fps),
          };
        }}
      />

      <Composition
        id="LowerThird"
        component={LowerThird}
        durationInFrames={150}
        fps={FPS}
        width={W}
        height={H}
        defaultProps={{ name: 'Neekhil Vatsa', role: 'Narad', accent: '#e94f37' }}
        calculateMetadata={metaFrom(150)}
      />

      <Composition
        id="CodeReveal"
        component={CodeReveal}
        durationInFrames={240}
        fps={FPS}
        width={W}
        height={H}
        defaultProps={{
          code: "const narad = () => 'remembered';\nconsole.log(narad());",
          bg: '#0b1020',
          accent: '#7ee787',
        }}
        calculateMetadata={metaFrom(240)}
      />

      {/* Escape hatch: render_remotion(component_tsx=...) overwrites generated/Custom.tsx. */}
      <Composition
        id="Custom"
        component={Custom}
        durationInFrames={150}
        fps={FPS}
        width={W}
        height={H}
        defaultProps={{}}
        calculateMetadata={metaFrom(150)}
      />
    </>
  );
};
