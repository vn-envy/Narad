import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from 'remotion';

export type CodeRevealProps = {
  code: string;
  bg: string;
  accent: string;
};

// Types the code out character by character, with a blinking caret.
export const CodeReveal: React.FC<CodeRevealProps> = ({ code, bg, accent }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const chars = code.length;
  const revealFrames = Math.max(1, Math.round(durationInFrames * 0.85));
  const shown = Math.floor(
    interpolate(frame, [0, revealFrames], [0, chars], { extrapolateRight: 'clamp' }),
  );
  const caretOn = Math.floor(frame / 8) % 2 === 0;

  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        padding: 100,
        fontFamily: 'Menlo, Consolas, monospace',
        fontSize: 40,
        lineHeight: 1.5,
        color: '#e6edf3',
      }}
    >
      <pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>
        {code.slice(0, shown)}
        <span style={{ color: accent, opacity: caretOn ? 1 : 0 }}>▋</span>
      </pre>
    </AbsoluteFill>
  );
};
