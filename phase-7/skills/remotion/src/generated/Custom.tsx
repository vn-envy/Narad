import { AbsoluteFill } from 'remotion';

// PLACEHOLDER — this file is overwritten by render_remotion(component_tsx=...).
// The escape hatch requires the authored TSX to `export const Custom: React.FC<...>`.
export type CustomProps = Record<string, unknown>;

export const Custom: React.FC<CustomProps> = () => {
  return (
    <AbsoluteFill
      style={{
        backgroundColor: '#0b1020',
        justifyContent: 'center',
        alignItems: 'center',
        fontFamily: 'Helvetica, Arial, sans-serif',
      }}
    >
      <h1 style={{ color: 'white', fontSize: 56, textAlign: 'center', padding: 80 }}>
        Custom composition — pass component_tsx to render_remotion to replace this.
      </h1>
    </AbsoluteFill>
  );
};
